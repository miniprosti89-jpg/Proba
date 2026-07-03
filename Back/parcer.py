import subprocess
import time
import os
import re
import sys
import textwrap
import requests
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

#from llama_cpp import Llama
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:1.5b"

#llm = Llama(model_path="./qwen2.5-3b.gguf")
# --- НАСТРОЙКИ И ПУТИ ---


def open_site(p, url):
    """Запуск браузера и переход на страницу (поддерживает Linux и Windows)."""
    DEBUG_PORT = "9222"

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
    """Каскад выбора названия товара: <title> → og:title → <h1> → фильтр + LLM.

    Порядок основан на надёжности сигнала. <title> почти всегда содержит
    название товара в начале (требование SEO). LLM — последний резерв,
    когда более надёжные сигналы не дали однозначного результата.
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

    # --- Шаг 4: отбрасываем служебные/мусорные заголовки ---
    candidates = [h for h in headings if looks_like_product_name(h["text"])]

    if len(candidates) == 1:
        print("  Каскад: единственный кандидат после фильтра")
        return candidates[0]["text"]

    if not candidates:
        print("  Каскад: кандидатов не осталось")
        return None

    # --- Шаг 5: несколько кандидатов → отдаём короткий список LLM ---
    print(f"  Каскад: {len(candidates)} кандидатов → спрашиваем LLM")
    return llm_pick_product_name(candidates)


def llm_pick_product_name(headings):
    """Отправляет все заголовки в LLM и просит выбрать название товара.
    Возвращает текст выбранного заголовка или None."""
    if not headings:
        return None

    items = ""
    for i, h in enumerate(headings):
        items += f"[{i}] ({h['tag']}): {h['text'][:200]}\n"

    prompt = f"""Тебе дан список заголовков со страницы маркетплейса.

Задача: выбрать тот, который является названием товара.

Название товара — конкретное описание продукта: тип, вкус, объём, вес, назначение, модель. Бренд необязателен.
НЕ название: служебные разделы («Описание», «Отзывы», «Доставка», «Гарантия», «Характеристики», «Похожие товары», «Преимущества», «Состав» и т.п.)

Примеры названий: "Хлорофилл жидкий со вкусом мяты, 500 мл", "Цинк пиколинат 25 мг 120 капсул", "Коллаген витамин С 200 г апельсин"

Заголовки:
{items}
Ответь только номером в квадратных скобках, например: [2]"""

    try:
        resp = requests.post(OLLAMA_URL, json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.0}
        }, timeout=60)
        resp.raise_for_status()
        answer = resp.json()["response"].strip()
        print(f"  LLM выбрал: {answer[:200]}")

        if "NONE" in answer.upper():
            return None
        match = re.search(r'\[(\d+)\]', answer)
        if match:
            idx = int(match.group(1))
            if 0 <= idx < len(headings):
                return headings[idx]["text"]
        return None
    except Exception as e:
        print(f"  Ошибка LLM-выбора названия: {e}")
        return None


def save_product_info(page):
    """Ищет название товара среди h1/h2 через LLM. Сохраняет в product_name.txt."""

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
        print(f"  LLM: название товара → {name[:80]}")
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



def screenshot_description_and_specs(page, path="description_section.png", selector=None, scroll_y=None, end_y=None):
    """Серия скриншотов с датой/временем, покрывающая раздел описания и характеристик.

    Heading-режим (scroll_y задан):
        Мотаем к началу блока (scroll_y - 200), затем каждые 300px делаем скриншот
        через make_main_screenshot пока не достигнем end_y (нижний край блока).

    Panel-режим (scroll_y=None):
        Прокручиваем панель в начало → скриншот, прокручиваем в конец → скриншот.
    """
    base, ext = path.rsplit(".", 1) if "." in path else (path, "png")

    if scroll_y is not None:
        # === Heading-режим ===
        page.evaluate(f"window.scrollTo(0, Math.max(0, {scroll_y} - 200))")
        page.wait_for_timeout(400)

        frame = 1
        while frame <= 20:
            frame_path = f"{base}_{frame}.{ext}"
            make_main_screenshot(page, path=frame_path)

            # Достигли нижней границы блока?
            current_bottom = page.evaluate("window.scrollY + window.innerHeight")
            if end_y is not None and current_bottom >= end_y:
                h=0
                while h < 2:
                    h=h+1
                    page.evaluate("window.scrollBy(0, 600)")
                    page.wait_for_timeout(300)
                    frame += 1
                    frame_path = f"{base}_{frame}.{ext}"
                    make_main_screenshot(page, path=frame_path)
                print(f"  Достигнут конец блока (end_y={end_y}), кадров: {frame}")
                break

            page.evaluate("window.scrollBy(0, 600)")
            page.wait_for_timeout(300)
            frame += 1

    else:
        # === Panel-режим ===
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

        page.evaluate(f"(() => {{ const p = ({FIND_PANEL_JS})(); if (p) p.scrollTop = 0; else window.scrollTo(0, 0); }})()")
        page.wait_for_timeout(400)
        make_main_screenshot(page, path=f"{base}_1.{ext}")

        page.evaluate(f"(() => {{ const p = ({FIND_PANEL_JS})(); if (p) p.scrollTop = p.scrollHeight; else window.scrollTo(0, document.body.scrollHeight); }})()")
        page.wait_for_timeout(400)
        make_main_screenshot(page, path=f"{base}_2.{ext}")

    return True


PICK_OVERLAY_JS = r"""
() => {
    const MARK = 'data-pd-overlay';

    const panel = document.createElement('div');
    panel.setAttribute(MARK, '1');
    panel.style.cssText = `
        position: fixed; top: 12px; right: 12px; z-index: 2147483647;
        background: #222; color: #fff; padding: 10px 14px; border-radius: 8px;
        font: 14px/1.4 sans-serif; box-shadow: 0 2px 10px rgba(0,0,0,.4);
        max-width: 320px;
    `;
    panel.innerHTML = `
        <div style="margin-bottom:8px;">Раскройте описание на странице (вкладки, "Показать полностью"), затем включите выбор и кликните на блок с описанием.</div>
        <button id="__pd_toggle" style="margin-right:6px;padding:6px 10px;cursor:pointer;">Выбрать блок описания</button>
        <button id="__pd_confirm" disabled style="padding:6px 10px;cursor:pointer;">Подтвердить</button>
    `;
    document.body.appendChild(panel);

    const toggleBtn = panel.querySelector('#__pd_toggle');
    const confirmBtn = panel.querySelector('#__pd_confirm');

    let pickMode = false;
    let hoverEl = null;
    let pickedEl = null;

    function isOverlay(el) {
        return !!(el && el.closest && el.closest(`[${MARK}]`));
    }

    function setOutline(el, color) {
        if (!el) return;
        el.style.outline = color ? `3px solid ${color}` : '';
        el.style.outlineOffset = color ? '-3px' : '';
    }

    function setPickMode(on) {
        pickMode = on;
        document.body.style.cursor = on ? 'crosshair' : '';
        toggleBtn.textContent = on ? 'Режим выбора включён (клик по блоку)' : 'Выбрать блок описания';
        toggleBtn.style.background = on ? '#c62828' : '';
    }

    toggleBtn.addEventListener('click', () => setPickMode(!pickMode));

    document.addEventListener('mouseover', (e) => {
        if (!pickMode || isOverlay(e.target)) return;
        if (hoverEl && hoverEl !== pickedEl) setOutline(hoverEl, null);
        hoverEl = e.target;
        if (hoverEl !== pickedEl) setOutline(hoverEl, 'orange');
    }, true);

    document.addEventListener('click', (e) => {
        if (!pickMode || isOverlay(e.target)) return;
        e.preventDefault();
        e.stopPropagation();
        if (pickedEl && pickedEl !== e.target) setOutline(pickedEl, null);
        pickedEl = e.target;
        setOutline(pickedEl, 'red');
        confirmBtn.disabled = false;
    }, true);

    confirmBtn.addEventListener('click', () => {
        if (!pickedEl) return;
        const rect = pickedEl.getBoundingClientRect();
        window.__pdResult = {
            text: (pickedEl.innerText || '').trim(),
            scrollY: Math.round(rect.top + window.scrollY),
            endScrollY: Math.round(rect.bottom + window.scrollY),
        };
        window.__pdConfirmed = true;
    });
}
"""

PICK_CLEANUP_JS = r"""
() => {
    document.querySelectorAll('[data-pd-overlay]').forEach(el => el.remove());
    document.body.style.cursor = '';
}
"""


def pick_description_manually(page):
    """Человек кликает в открытом браузере на блок с описанием товара.

    Клик только подсвечивает кандидата; описание фиксируется отдельной
    кнопкой "Подтвердить" в оверлее, чтобы случайный клик не сработал как выбор.
    До включения режима выбора обычные клики по странице (вкладки, "Показать
    полностью") проходят как есть — человек может сначала раскрыть текст.
    """
    print("\n--- Ожидание ручного выбора блока описания в браузере ---")
    page.evaluate(PICK_OVERLAY_JS)
    page.wait_for_function("window.__pdConfirmed === true", timeout=0)
    result = page.evaluate("window.__pdResult")
    page.evaluate(PICK_CLEANUP_JS)

    text = result["text"]
    print(f"  Выбрано человеком: {text[:80]}...")
    with open(back_dir / "description.txt", "w", encoding="utf-8", errors="replace") as f:
        f.write(text)

    screenshot_description_and_specs(
        page, str(back_dir / "description_section.png"),
        scroll_y=result.get("scrollY"), end_y=result.get("endScrollY")
    )


# --- ЗАПУСК ---
if __name__ == "__main__":
    target_url = (sys.argv)[1]
    #
    with sync_playwright() as p:
        browser, page = open_site(p, target_url)

        # Выполняем первую часть задач
        time.sleep(3)
        save_product_info(page)
        make_main_screenshot(page)

        # Выполняем вторую часть — ручной выбор блока описания человеком
        pick_description_manually(page)
    """
        current_dir = os.path.dirname(os.path.abspath(__file__))
        compiler_path = os.path.join(current_dir, 'compiler.py')
        comp_res = subprocess.run(['python', compiler_path], capture_output=True, text=True)
        print(comp_res.stdout)
    """