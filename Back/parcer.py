import subprocess
import time
import os
import re
import sys
import json
from io import BytesIO
from datetime import datetime
from playwright.sync_api import sync_playwright
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

back_dir = Path(__file__).parent

# Консоль Windows (cp1251) не умеет выводить символы вне своей кодировки.
# Переключаем stdout/stderr на UTF-8 с заменой неизвестных символов на '?'.
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='cp1251', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='cp1251', errors='replace')

# --- НАСТРОЙКИ И ПУТИ ---

DEBUG_PORT = "9222"


def open_site(p, url):
    """Запуск браузера и переход на страницу (поддерживает Linux и Windows)."""
    # Если браузер с прошлого запуска ещё жив (отладочный порт уже отвечает) —
    # переиспользуем его и просто открываем новую вкладку, вместо того чтобы
    # плодить новые окна и терять куки/сессию сайта.
    try:
        browser = p.chromium.connect_over_cdp(f"http://localhost:{DEBUG_PORT}", timeout=2000)
        print("Найден уже запущенный браузер — открываем новую вкладку.")
    except Exception:
        browser = None

    if browser is None:
        # Определяем путь к браузеру и папку пользовательских данных в зависимости от ОС
        if os.name == "posix":  # Linux / macOS
            chrome_path = "chromium"  # предполагается, что chromium установлен в PATH
            user_data_dir = "/tmp/playwright-profile"
        elif os.name == "nt":  # Windows
            possible_paths = [
                # Google Chrome
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
                os.path.expandvars(r"%LOCALAPPDATA%\Chromium\Application\chrome.exe"),
                # Microsoft Edge (Chromium) — на Windows обычно предустановлен
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                # Яндекс.Браузер (Chromium)
                os.path.expandvars(r"%LOCALAPPDATA%\Yandex\YandexBrowser\Application\browser.exe"),
                r"C:\Program Files\Yandex\YandexBrowser\Application\browser.exe",
                r"C:\Program Files (x86)\Yandex\YandexBrowser\Application\browser.exe",
                # Chromium (открытый движок, ставится в профиль пользователя)
                os.path.expandvars(r"%LOCALAPPDATA%\Chromium\Application\chrome.exe"),
            ]
            chrome_path = None
            for path in possible_paths:
                if os.path.exists(path):
                    chrome_path = path
                    break
            if chrome_path is None:
                raise FileNotFoundError("Не найден ни один поддерживаемый браузер (Chrome/Edge/Яндекс). Проверьте пути в possible_paths.")
            # Один и тот же профиль на все запуски — сохраняет куки и сессию
            # сайта между запусками (иначе каждый заход выглядит первым).
            user_data_dir = r"C:\Temp\chrome-debug"
        else:
            raise OSError(f"Unsupported OS: {os.name}")

        print(f"Запуск браузера: {chrome_path}")
        # Запускаем браузер с отладочным портом
        subprocess.Popen([
            chrome_path,
            f"--remote-debugging-port={DEBUG_PORT}",
            f"--user-data-dir={user_data_dir}",
            "--start-maximized"
        ])

        print("Ожидание запуска браузера...")
        last_error = None
        for attempt in range(30):
            time.sleep(1)
            try:
                browser = p.chromium.connect_over_cdp(f"http://localhost:{DEBUG_PORT}")
                break
            except Exception as e:
                last_error = e
        if browser is None:
            raise RuntimeError(
                f"Не удалось подключиться к браузеру ({chrome_path}) за 30с. "
                f"Закройте все окна этого браузера и попробуйте снова. "
                f"Последняя ошибка: {last_error}"
            )

    context = browser.contexts[0] if browser.contexts else browser.new_context()
    page = context.new_page()

    # Устанавливаем размер окна для стандартного скриншота
    page.set_viewport_size({"width": 1920, "height": 1080})

    print(f"Переход по ссылке: {url}")
    page.goto(url, wait_until="domcontentloaded")

    load_full_page(page)

    return browser, page


def load_full_page(page, step=800, pause=200, max_scrolls=50):
    """Прокручивает страницу до самого низа и обратно наверх.

    Многие сайты дорендеривают контент (в т.ч. раздел описания) лениво,
    по мере прокрутки — пока пользователь не долистает страницу, часть
    DOM просто не существует. Прокручиваем до конца, чтобы всё успело
    подгрузиться, затем возвращаемся в начало перед первым скриншотом.
    """
    print("Прокрутка страницы до конца для подгрузки ленивого контента...")
    for _ in range(max_scrolls):
        prev_y = page.evaluate("window.scrollY")
        page.evaluate(f"window.scrollBy(0, {step})")
        page.wait_for_timeout(pause)
        current_y = page.evaluate("window.scrollY")
        at_bottom = page.evaluate("window.scrollY + window.innerHeight >= document.body.scrollHeight - 2")
        if at_bottom or current_y == prev_y:
            break
    page.wait_for_timeout(400)

    print("Возврат в начало страницы...")
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(400)


def reconnect_to_active_tab(p, target_url):
    """Подключается к уже запущенному отладочному браузеру и находит вкладку,
    оставленную фазой 1 для target_url. НЕ вызывает page.goto/load_full_page —
    использует страницу как есть (текущий скролл пользователя).

    Основной сигнал — JS-метка window.__parcer_active_tab, которую фаза 1
    ставит на свою вкладку. Резервный сигнал — совпадение URL (на случай,
    если метка слетела из-за полной перезагрузки страницы).
    """
    try:
        browser = p.chromium.connect_over_cdp(f"http://localhost:{DEBUG_PORT}", timeout=5000)
    except Exception as e:
        raise RuntimeError(
            "Не удалось подключиться к браузеру. Похоже, окно браузера было закрыто. "
            "Перезапустите обработку этой ссылки с фазы 1."
        ) from e

    context = browser.contexts[0] if browser.contexts else None
    pages = context.pages if context else []

    candidates = []
    for pg in pages:
        try:
            if pg.evaluate("() => window.__parcer_active_tab === true"):
                candidates.append(pg)
        except Exception:
            continue  # вкладка могла закрыться между enumerate и evaluate

    if not candidates:
        from urllib.parse import urlparse
        target = urlparse(target_url)
        target_key = (target.netloc, target.path.rstrip('/'))
        for pg in pages:
            try:
                u = urlparse(pg.url)
            except Exception:
                continue
            if (u.netloc, u.path.rstrip('/')) == target_key:
                candidates.append(pg)

    if not candidates:
        raise RuntimeError(
            "Не найдена вкладка с фазы 1 для этой ссылки. Возможно, вкладка была закрыта. "
            "Перезапустите обработку этой ссылки с фазы 1."
        )

    if len(candidates) > 1:
        print(f"  Предупреждение: найдено {len(candidates)} подходящих вкладок, "
              f"используем последнюю из списка. URL кандидатов: "
              f"{[c.url for c in candidates]}")

    page = candidates[-1]
    page.evaluate("() => { window.__parcer_active_tab = true; }")  # защитный ре-тег
    return browser, page


SERVICE_WORDS = [
    "описание", "доставка", "возврат", "оплата", "отзыв", "гарантия",
    "характеристики", "похожие товары", "с этим товаром", "подборки",
    "преимущества", "состав", "инструкция", "вопросы и ответы", "о товаре",
    "рекомендуем", "вам может понравиться", "хиты продаж", "акции",
    "валюта", "покупателям", "продавцам", "наши проекты", "компания",
    "приложение", "каталог", "корзина", "избранное",
]

# Слоганы сайта в og:title / <title>, которые не являются названием товара.
GENERIC_MARKERS = [
    "широкий ассортимент", "скидки каждый день", "ассортимент товаров",
    "официальный сайт", "лучшие цены", "купить онлайн", "каталог товаров",
    "интернет-магазин", "online store",
]


def is_service_heading(text):
    """True, если заголовок — служебный раздел сайта, а не название товара."""
    t = text.strip().lower()
    return any(sw in t for sw in SERVICE_WORDS)


def is_generic_tagline(text):
    """True, если текст — это общий слоган сайта, а не название товара."""
    t = text.strip().lower()
    return any(m in t for m in GENERIC_MARKERS)


def looks_like_product_name(text):
    """Грубая проверка: похож ли текст на название товара."""
    t = text.strip()
    if len(t) < 10:
        return False
    if is_service_heading(t) or is_generic_tagline(t):
        return False
    # Чистое число / рейтинг / цена
    if re.fullmatch(r'[\d\s.,%?₽$]+', t):
        return False
    # Нет ни одного нормального слова
    if not re.search(r'[A-Za-zА-Яа-яЁё]{3,}', t):
        return False
    return True


def extract_from_title(title):
    """Вытаскивает название товара из <title>.

    Типичный <title> страницы товара:
      "Название товара БРЕНД 123456 купить за 2 843 ₽ в интернет-магазине Сайт"
    Отрезаем имя сайта (по разделителям), хвост "купить ...", "за NNN ..."
    и артикул (длинную последовательность цифр).
    """
    if not title:
        return ""
    seg = title.strip()
    # Убираем ведущее "Купить "
    seg = re.sub(r'^купить\s+', '', seg, flags=re.IGNORECASE)
    # Отрезаем хвост "... купить ..." и "... за NNN ..."
    seg = re.split(r'\s+купить\b', seg, flags=re.IGNORECASE)[0]
    seg = re.split(r'\s+за\s+\d', seg, flags=re.IGNORECASE)[0]
    # Отрезаем артикул и всё после него
    seg = re.sub(r'\s+\d{5,}.*$', '', seg)
    # Если остались разделители имени сайта (— | – /) — берём самый длинный сегмент.
    # Обычный дефис не используем: он встречается внутри названий товаров.
    parts = re.split(r'\s[—|–/]\s', seg)
    if len(parts) > 1:
        seg = max(parts, key=len)
    # Зачищаем концы от висящих разделителей (напр. "– " перед отрезанным "купить")
    return seg.strip(" \t\r\n–—|/·•")


def pick_product_name(headings, og_title, doc_title):
    """Каскад выбора названия товара: <title> → og:title → <h1> → фильтр.

    Порядок основан на надёжности сигнала. <title> почти всегда содержит
    название товара в начале (требование SEO).
    """
    h1s = [h for h in headings if h["tag"] == "h1"]

    # --- Шаг 1: <title> — самый надёжный сигнал на странице товара ---
    title_name = extract_from_title(doc_title)
    if looks_like_product_name(title_name):
        print(f"  Каскад: из <title> → {title_name[:80]}")
        return title_name

    # --- Шаг 2: мета-тег og:title (если это не слоган сайта) ---
    if og_title and looks_like_product_name(og_title):
        print(f"  Каскад: из og:title → {og_title[:80]}")
        return og_title

    # --- Шаг 3: единственный <h1>, если похож на название ---
    if len(h1s) == 1 and looks_like_product_name(h1s[0]["text"]):
        print(f"  Каскад: единственный <h1> → {h1s[0]['text'][:80]}")
        return h1s[0]["text"]

    # --- Шаг 4: отбрасываем служебные/мусорные заголовки, берём первого кандидата ---
    candidates = [h for h in headings if looks_like_product_name(h["text"])]

    if not candidates:
        print("  Каскад: кандидатов не осталось")
        return None

    if len(candidates) > 1:
        print(f"  Каскад: {len(candidates)} кандидатов, берём первый (h1 приоритетнее h2)")
    else:
        print("  Каскад: единственный кандидат после фильтра")
    return candidates[0]["text"]


def save_product_info(page):
    """Ищет название товара среди h1/h2 эвристикой. Сохраняет в product_name.txt."""

    try:
        page.wait_for_selector('h1, h2', timeout=10000)
    except Exception:
        print("  Предупреждение: h1/h2 не появились за 10с, пробуем всё равно.")

    data = page.evaluate("""() => {
        const result = [];
        for (const tag of ['h1', 'h2']) {
            for (const el of document.querySelectorAll(tag)) {
                const text = (el.innerText || el.textContent || '').trim();
                if (text.length < 3) continue;
                result.push({ tag, text });
            }
        }
        const ogEl = document.querySelector('meta[property="og:title"]');
        return {
            headings: result,
            ogTitle: ogEl ? (ogEl.content || '').trim() : '',
            docTitle: (document.title || '').trim()
        };
    }""")

    headings = data["headings"]
    og_title = data["ogTitle"]
    doc_title = data["docTitle"]

    h1s = [h for h in headings if h['tag'] == 'h1']
    h2s = [h for h in headings if h['tag'] == 'h2']
    print(f"  Найдено заголовков: h1={len(h1s)}, h2={len(h2s)}")
    for h in headings:
        print(f"    [{h['tag']}] {h['text'][:100]}")
    print(f"  og:title: {og_title[:100]}")
    print(f"  <title>: {doc_title[:100]}")

    name = pick_product_name(headings, og_title, doc_title)

    if name:
        print(f"  Название товара → {name[:80]}")
        with open(back_dir / "product_name.txt", "w", encoding="utf-8", errors="replace") as f:
            f.write(name)
    else:
        print("  Название товара не найдено.")
        with open(back_dir / "product_name.txt", "w", encoding="utf-8", errors="replace") as f:
            f.write("Название не найдено")


def make_main_screenshot(page, path=None):
    """Делает скриншот и накладывает дату/время.
    path=None → сохраняет в back_dir/final_screenshot.png (поведение по умолчанию).
    """
    screenshot_bytes = page.screenshot(full_page=False)
    image = Image.open(BytesIO(screenshot_bytes))
    draw = ImageDraw.Draw(image)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Создаём временное изображение для рендера текста
    font = ImageFont.load_default()
    tmp_bbox = draw.textbbox((0, 0), timestamp, font=font)
    tmp_w = tmp_bbox[2] - tmp_bbox[0]
    tmp_h = tmp_bbox[3] - tmp_bbox[1]
    
    # Рендерим текст на отдельном изображении
    txt_image = Image.new('RGBA', (tmp_w + 10, tmp_h + 10), (0, 0, 0, 0))
    txt_draw = ImageDraw.Draw(txt_image)
    txt_draw.text((5, 5), timestamp, fill="red", font=font)
    
    # Масштабируем текст (scale_factor = 3, 4, 5 — чем больше, тем крупнее)
    scale_factor = 4
    txt_image = txt_image.resize(
        (txt_image.width * scale_factor, txt_image.height * scale_factor),
        Image.Resampling.LANCZOS
    )
    
    # Накладываем масштабированный текст на скриншот
    txt_x = 60
    txt_y = image.height - txt_image.height - 20
    image.paste(txt_image, (txt_x, txt_y), txt_image)

    if path is None:
        path = back_dir / "description_section_0.png"
    image.save(path)
    print(f"Скриншот сохранён: {path}")


FIND_PANEL_JS = """() => {
    let best = null, bestArea = 0;
    for (const el of document.querySelectorAll('*')) {
        if (el === document.body || el === document.documentElement) continue;
        const oy = getComputedStyle(el).overflowY;
        if (oy !== 'auto' && oy !== 'scroll') continue;
        if (el.scrollHeight <= el.clientHeight + 50) continue;
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height < 200) continue;
        if (r.bottom < 0 || r.top > window.innerHeight) continue;
        const area = r.width * r.height;
        if (area > bestArea) { bestArea = area; best = el; }
    }
    return best;
}"""


def get_scroll_context(page):
    """Определяет, что сейчас скроллится: открытая боковая панель
    (overflow: auto/scroll, самая большая по площади) или обычная страница.
    Определяется заново при каждом вызове — ничего не сохраняется между
    вызовами, т.к. панель может быть закрыта/перерисована между ними."""
    return page.evaluate(f"""() => {{
        const findPanel = {FIND_PANEL_JS};
        const p = findPanel();
        if (p) {{
            return {{ type: 'panel', scrollTop: p.scrollTop, clientHeight: p.clientHeight }};
        }}
        return {{ type: 'window', scrollTop: window.scrollY, clientHeight: window.innerHeight }};
    }}""")


def scroll_context_by(page, delta):
    """Прокручивает текущий контекст (открытую панель или страницу) на delta пикселей."""
    page.evaluate(f"""() => {{
        const findPanel = {FIND_PANEL_JS};
        const p = findPanel();
        if (p) p.scrollTop += {delta};
        else window.scrollBy(0, {delta});
    }}""")


def scroll_context_to(page, y):
    """Прокручивает текущий контекст (открытую панель или страницу) к позиции y."""
    page.evaluate(f"""() => {{
        const findPanel = {FIND_PANEL_JS};
        const p = findPanel();
        if (p) p.scrollTop = Math.max(0, {y});
        else window.scrollTo(0, Math.max(0, {y}));
    }}""")


def screenshot_description_and_specs(page, path, start_y, end_y):
    """Серия скриншотов с датой/временем между двумя отметками человека
    (start_y/end_y). Работает одинаково для обычной страницы и для открытой
    боковой панели — прокрутка идёт через scroll_context_to/scroll_context_by,
    которые сами определяют актуальный контекст на каждом шаге.
    """
    base, ext = path.rsplit(".", 1) if "." in path else (path, "png")

    scroll_context_to(page, max(0, start_y - 200))
    page.wait_for_timeout(400)

    frame = 1
    while frame <= 20:
        frame_path = f"{base}_{frame}.{ext}"
        make_main_screenshot(page, path=frame_path)

        # Достигли нижней границы отмеченной полосы?
        ctx = get_scroll_context(page)
        current_bottom = ctx["scrollTop"] + ctx["clientHeight"]
        if current_bottom >= end_y:
            h = 0
            while h < 2:
                h += 1
                scroll_context_by(page, 600)
                page.wait_for_timeout(300)
                frame += 1
                frame_path = f"{base}_{frame}.{ext}"
                make_main_screenshot(page, path=frame_path)
            print(f"  Достигнут конец отмеченной полосы (end_y={end_y}), кадров: {frame}")
            break

        scroll_context_by(page, 600)
        page.wait_for_timeout(300)
        frame += 1

    return True


def collect_band_text(page, start_y, end_y, is_panel):
    """Собирает текст всех элементов, у которых бо́льшая часть высоты (не
    любое пересечение) лежит в полосе [start_y, end_y] — координаты в той же
    системе, что и отметки human'а (панель или документ).

    Порог в 50% защищает от двух проблем: слишком широкий контейнер (задевает
    полосу лишь краем) не проходит порог и уступает место своим детям, а
    абзац, ровно на границе которого стоит отметка человека, всё равно
    попадает — обычно бо́льшая его часть уже внутри полосы.

    Среди прошедших порог элементов оставляем только самые внешние — если
    один элемент содержит другой, конкретно этот другой не дублируется.
    Для панели кандидаты дополнительно ограничены её потомками (иначе
    координаты «внутри панели» и «в документе» могут случайно совпасть по
    числу и всё перепутать).
    """
    return page.evaluate("""(args) => {
        const [startY, endY, isPanel] = args;
        const findPanel = %s;
        const panel = isPanel ? findPanel() : null;
        const panelRect = panel ? panel.getBoundingClientRect() : null;

        function elementBand(el) {
            const r = el.getBoundingClientRect();
            if (isPanel && panel) {
                return {
                    top: r.top - panelRect.top + panel.scrollTop,
                    bottom: r.bottom - panelRect.top + panel.scrollTop
                };
            }
            return { top: r.top + window.scrollY, bottom: r.bottom + window.scrollY };
        }

        const candidates = Array.from(document.querySelectorAll('div, p, section, article, td, li'));
        const scoped = (isPanel && panel) ? candidates.filter(el => panel.contains(el)) : candidates;

        const inBand = [];
        for (const el of scoped) {
            const { top, bottom } = elementBand(el);
            const height = bottom - top;
            if (height <= 0) continue;
            const overlap = Math.min(bottom, endY) - Math.max(top, startY);
            if (overlap <= 0) continue;
            if (overlap / height < 0.5) continue;
            const text = (el.innerText || '').trim();
            if (!text) continue;
            inBand.push({ el, text });
        }

        inBand.sort((a, b) => {
            const pos = a.el.compareDocumentPosition(b.el);
            if (pos & Node.DOCUMENT_POSITION_FOLLOWING) return -1;
            if (pos & Node.DOCUMENT_POSITION_PRECEDING) return 1;
            return 0;
        });

        const chosen = [];
        for (const cand of inBand) {
            if (chosen.some(c => c.el !== cand.el && c.el.contains(cand.el))) continue;
            for (let i = chosen.length - 1; i >= 0; i--) {
                if (cand.el !== chosen[i].el && cand.el.contains(chosen[i].el)) chosen.splice(i, 1);
            }
            chosen.push(cand);
        }

        const finalTexts = [];
        for (const c of chosen) {
            if (finalTexts.some(t => t.includes(c.text))) continue;
            finalTexts.push(c.text);
        }
        return finalTexts.join('\\n\\n');
    }""" % FIND_PANEL_JS, [start_y, end_y, is_panel])


def run_phase1(url):
    """Фаза 1: открыть/переиспользовать браузер, перейти по ссылке, найти
    название товара, сделать главный скриншот. Описание НЕ ищет — человек
    сам отметит его начало и конец на следующих шагах. Браузер остаётся
    открытым после завершения процесса (см. open_site)."""
    with sync_playwright() as p:
        browser, page = open_site(p, url)
        time.sleep(3)
        save_product_info(page)
        make_main_screenshot(page)
        page.evaluate("() => { window.__parcer_active_tab = true; }")
        print("Фаза 1 завершена. Переключитесь на окно браузера и прокрутите "
              "страницу до начала описания товара, затем продолжите обработку.")


def run_mark_start(url):
    """Отметка «начало здесь»: переподключается к вкладке фазы 1, определяет
    текущий контекст прокрутки (панель или страница) и сохраняет позицию
    прямо на странице — переживает переподключение так же, как метка вкладки
    (page.goto здесь не вызывается)."""
    with sync_playwright() as p:
        browser, page = reconnect_to_active_tab(p, url)
        ctx = get_scroll_context(page)
        page.evaluate(f"""() => {{
            window.__parcer_start_y = {ctx['scrollTop']};
            window.__parcer_start_type = {json.dumps(ctx['type'])};
        }}""")
        print(f"Начало описания отмечено: тип={ctx['type']}, y={ctx['scrollTop']}. "
              "Прокрутите страницу до конца описания и продолжите обработку.")


def run_mark_end(url):
    """Отметка «конец здесь»: переподключается к вкладке, определяет текущий
    контекст прокрутки, сверяет его с контекстом на момент «начало здесь» —
    и, если он не совпал (например, панель успели закрыть), останавливается
    с понятной ошибкой, а не молча путает координаты. При совпадении —
    собирает текст полосы [начало, конец] и делает серию скриншотов."""
    with sync_playwright() as p:
        browser, page = reconnect_to_active_tab(p, url)

        start = page.evaluate("""() => ({
            y: window.__parcer_start_y,
            type: window.__parcer_start_type
        })""")
        if start.get("y") is None or start.get("type") is None:
            raise RuntimeError(
                "Не найдена отметка «Начало здесь». Сначала нажмите «Начало "
                "здесь», затем «Конец здесь»."
            )

        ctx = get_scroll_context(page)
        if ctx["type"] != start["type"]:
            raise RuntimeError(
                "Похоже, боковая панель с описанием была закрыта (или, наоборот, "
                "открылась) между отметками «Начало здесь» и «Конец здесь». "
                "Начните заново с «Начало здесь»."
            )

        is_panel = ctx["type"] == "panel"
        start_y = start["y"]
        end_y = ctx["scrollTop"] + ctx["clientHeight"]

        text = collect_band_text(page, start_y, end_y, is_panel)
        with open(back_dir / "description.txt", "w", encoding="utf-8", errors="replace") as f:
            f.write(text)

        screenshot_description_and_specs(
            page, str(back_dir / "description_section.png"),
            start_y=start_y, end_y=end_y
        )


def run_skip():
    """Путь 'на странице нет описания': пустой description.txt, без
    дополнительных скриншотов (кроме уже сделанного description_section_0.png
    из фазы 1). Не требует браузера."""
    with open(back_dir / "description.txt", "w", encoding="utf-8", errors="replace") as f:
        f.write("")
    print("Описание пропущено по решению пользователя.")


# --- ЗАПУСК ---
if __name__ == "__main__":
    target_url = sys.argv[1]
    # sys.argv[2] (criteria_str) передаётся для симметрии с compiler.py/word_redactor.py,
    # parcer.py его не использует.
    phase = sys.argv[3] if len(sys.argv) > 3 else None

    if phase == "phase1":
        run_phase1(target_url)
    elif phase == "mark_start":
        run_mark_start(target_url)
    elif phase == "mark_end":
        run_mark_end(target_url)
    elif phase == "skip":
        run_skip()
    elif phase is None:
        run_phase1(target_url)
        run_mark_start(target_url)
        run_mark_end(target_url)
    else:
        print(f"Неизвестная фаза: {phase!r}. Ожидается phase1, mark_start, mark_end, skip или без аргумента.")
        sys.exit(1)