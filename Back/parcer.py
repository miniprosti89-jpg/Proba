import subprocess
import time
import os
import re
import sys
import textwrap
import requests
from io import BytesIO
from datetime import datetime
from playwright.sync_api import sync_playwright, Error
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
# launcher.py прописывает OLLAMA_BASE_URL в окружение перед запуском Streamlit
# (parcer.py наследует его как подпроцесс) — актуально, если портативная
# Ollama поднялась на резервном порту из-за занятого 11434.
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_URL = OLLAMA_BASE_URL + "/api/generate"
OLLAMA_TAGS_URL = OLLAMA_BASE_URL + "/api/tags"
OLLAMA_MODEL = "qwen2.5:1.5b"
# На слабом железе (тестовые машины без GPU) генерация может занимать дольше
# 60с — держим запас. keep_alive не даёт Ollama выгружать модель из памяти
# между вызовами (дефолт — 5 минут простоя), иначе следующий запрос ждёт
# ещё и повторную загрузку модели с диска поверх генерации.
OLLAMA_TIMEOUT = 180
OLLAMA_KEEP_ALIVE = "30m"

#llm = Llama(model_path="./qwen2.5-3b.gguf")
# --- НАСТРОЙКИ И ПУТИ ---

_ollama_available = None  # None = ещё не проверяли; True/False = результат проверки


def ollama_model_available():
    """Проверяет один раз за запуск, что OLLAMA_MODEL реально загружена в Ollama.

    Без этой проверки каждый из трёх LLM-вызовов в парсере узнавал бы об
    отсутствии модели только по 404 от /api/generate (например, если модель
    из parcer.py разошлась с моделью, прогретой в launcher.py, или если
    портативная Ollama на слабой машине видит только часть моделей через
    переменную окружения OLLAMA_MODELS). Результат кэшируется на весь запуск.
    """
    global _ollama_available
    if _ollama_available is not None:
        return _ollama_available

    try:
        resp = requests.get(OLLAMA_TAGS_URL, timeout=5)
        resp.raise_for_status()
        names = {m.get("name", "") for m in resp.json().get("models", [])}
        _ollama_available = OLLAMA_MODEL in names
        if not _ollama_available:
            print(f"  [Ollama] Модель '{OLLAMA_MODEL}' не найдена среди установленных: {sorted(names)}.")
            print("  [Ollama] LLM-подсказки отключены на этот запуск, используются только эвристики.")
    except Exception as e:
        print(f"  [Ollama] Сервер недоступен ({e}).")
        print("  [Ollama] LLM-подсказки отключены на этот запуск, используются только эвристики.")
        _ollama_available = False

    return _ollama_available


def _log_ollama_error(prefix, e):
    """Печатает ошибку запроса к Ollama вместе с телом ответа сервера.

    Текст requests.exceptions.HTTPError сам по себе — это только код и URL
    ("404 Client Error: Not Found for url: ..."), без причины. Ollama обычно
    кладёт человекочитаемую причину в тело JSON-ответа (например, что blob
    модели отсутствует) — без него причину пришлось бы искать в ollama.log.
    """
    print(f"{prefix}: {e}")
    resp = getattr(e, "response", None)
    if resp is not None:
        print(f"{prefix} (тело ответа Ollama): {resp.text[:500]}")


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
                # Яндекс.Браузер (Chromium)
                os.path.expandvars(r"%LOCALAPPDATA%\Yandex\YandexBrowser\Application\browser.exe"),
                r"C:\Program Files\Yandex\YandexBrowser\Application\browser.exe",
                r"C:\Program Files (x86)\Yandex\YandexBrowser\Application\browser.exe",
                # Google Chrome
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
                os.path.expandvars(r"%LOCALAPPDATA%\Chromium\Application\chrome.exe"),
                # Microsoft Edge (Chromium) — на Windows обычно предустановлен
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
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


def _safe_evaluate(page, expression, default=None):
    """page.evaluate, устойчивый к самопроизвольной навигации сайта.

    Некоторые сайты (например Wildberries с targetUrl-редиректами) сами
    уходят на другой URL прямо во время нашей прокрутки — тогда execution
    context уничтожается и evaluate падает с "Execution context was
    destroyed". Ждём, пока страница долетит до load, и пробуем ещё раз.
    """
    try:
        return page.evaluate(expression)
    except Error as e:
        if "Execution context was destroyed" not in str(e) and "context was destroyed" not in str(e):
            raise
        try:
            page.wait_for_load_state("load", timeout=15000)
        except Error:
            pass
        try:
            return page.evaluate(expression)
        except Error:
            return default


def load_full_page(page, step=800, pause=200, max_scrolls=50):
    """Прокручивает страницу до самого низа и обратно наверх.

    Многие сайты дорендеривают контент (в т.ч. раздел описания) лениво,
    по мере прокрутки — пока пользователь не долистает страницу, часть
    DOM просто не существует. Прокручиваем до конца, чтобы всё успело
    подгрузиться, затем возвращаемся в начало перед первым скриншотом.
    """
    print("Прокрутка страницы до конца для подгрузки ленивого контента...")
    for _ in range(max_scrolls):
        prev_y = _safe_evaluate(page, "window.scrollY", 0)
        _safe_evaluate(page, f"window.scrollBy(0, {step})")
        page.wait_for_timeout(pause)
        current_y = _safe_evaluate(page, "window.scrollY", prev_y)
        at_bottom = _safe_evaluate(
            page,
            "window.scrollY + window.innerHeight >= document.body.scrollHeight - 2",
            False,
        )
        if at_bottom or current_y == prev_y:
            break
    page.wait_for_timeout(400)

    print("Возврат в начало страницы...")
    _safe_evaluate(page, "window.scrollTo(0, 0)")
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
    if not ollama_model_available():
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
            "keep_alive": OLLAMA_KEEP_ALIVE,
            "options": {"temperature": 0.0}
        }, timeout=OLLAMA_TIMEOUT)
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
        _log_ollama_error("  Ошибка LLM-выбора названия", e)
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


def extract_text_blocks(page):
    """Извлекает текстовые блоки со страницы с их CSS-путями."""
    return page.evaluate("""() => {
        const blocks = [];
        const seen = new Set();

        function getCssPath(el) {
            const parts = [];
            while (el && el.nodeType === 1) {
                let selector = el.tagName.toLowerCase();
                if (el.id) {
                    selector += '#' + el.id;
                    parts.unshift(selector);
                    break;
                }
                if (el.className && typeof el.className === 'string') {
                    const cls = el.className.trim().split(/\\s+/).filter(c => c.length > 0 && !c.match(/^\\d/));
                    if (cls.length > 0) selector += '.' + cls[0];
                }
                const parent = el.parentElement;
                if (parent) {
                    const siblings = [...parent.children].filter(c => c.tagName === el.tagName);
                    if (siblings.length > 1) {
                        const idx = siblings.indexOf(el) + 1;
                        selector += ':nth-of-type(' + idx + ')';
                    }
                }
                parts.unshift(selector);
                el = parent;
            }
            return parts.join(' > ');
        }

        // Ищем элементы с текстом
        const candidates = document.querySelectorAll('p, div, span, section, article, td, li');
        for (const el of candidates) {
            // Пропускаем элементы с большим количеством дочерних элементов (контейнеры)
            if (el.children.length > 1) continue;

            const text = el.innerText?.trim();
            if (!text || text.length < 30) continue;
            if (seen.has(text)) continue;
            seen.add(text);

            // Обрезаем превью до 200 символов
            const preview = text.substring(0, 200);
            const cssPath = getCssPath(el);

            blocks.push({
                index: blocks.length,
                preview: preview,
                selector: cssPath,
                length: text.length
            });

            if (blocks.length >= 50) break;
        }
        return blocks;
    }""")


def is_prose(text):
    """Определяет, является ли текст связным описанием (а не характеристиками)."""
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    if not lines:
        return False, 0

    # Считаем предложения (точки, восклицательные, вопросительные знаки)
    sentence_count = len(re.findall(r'[.!?]\s', text)) + (1 if text.rstrip()[-1:] in '.!?' else 0)

    # Средняя длина строки
    avg_line_len = sum(len(l) for l in lines) / len(lines)

    # Доля коротких строк (< 40 символов) — у характеристик много коротких строк
    short_lines = sum(1 for l in lines if len(l) < 40)
    short_ratio = short_lines / len(lines)

    # Доля строк с двоеточиями (ключ: значение) — типично для характеристик
    colon_lines = sum(1 for l in lines if ':' in l and len(l) < 80)
    colon_ratio = colon_lines / len(lines) if lines else 0

    # Оценка: чем выше — тем больше похоже на описание
    score = 0
    score += min(sentence_count * 10, 40)    # много предложений = описание
    score += min(avg_line_len / 2, 30)       # длинные строки = описание
    score -= short_ratio * 30                # много коротких строк = характеристики
    score -= colon_ratio * 30                # много двоеточий = характеристики

    return score > 20, score


def filter_description_blocks(blocks):
    """Фильтрует блоки, оставляя только те, что похожи на описание."""
    scored = []
    for b in blocks:
        is_desc, score = is_prose(b["preview"])
        if is_desc:
            b["score"] = score
            scored.append(b)
            print(f"  Блок [{b['index']}] score={score:.0f}: {b['preview'][:60]}...")
        else:
            print(f"  Блок [{b['index']}] отброшен (score={score:.0f}): {b['preview'][:60]}...")

    # Сортируем по score (лучшие первыми)
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


def ask_llm_to_pick(blocks):
    """Отправляет пронумерованный список текстовых блоков в LLM для выбора."""
    if not ollama_model_available():
        return None

    # Формируем список для LLM
    block_list = ""
    for b in blocks:
        block_list += f"[{b['index']}] ({b['length']} chars): {b['preview']}\n\n"

    prompt = f"""Here are text blocks from a product page. Pick the one that is the product DESCRIPTION.

DESCRIPTION = long prose text about the product: what it is, how it works, benefits, ingredients.
Example: "Коллаген – незаменимый для организма компонент, который требует постоянного восполнения..."

NOT description:
- Characteristics/specs: "Тип: Пищевая добавка", "Вес: 200г"
- Reviews, prices, navigation, delivery info

Blocks:
{block_list}

If you found the description, reply with ONLY the number. Example: 5
If none of these blocks is a description, reply with: NONE"""

    try:
        resp = requests.post(OLLAMA_URL, json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "keep_alive": OLLAMA_KEEP_ALIVE,
            "options": {"temperature": 0.1}
        }, timeout=OLLAMA_TIMEOUT)
        resp.raise_for_status()
        answer = resp.json()["response"].strip()
        print(f"Ответ LLM: {answer}")

        # Ищем число в ответе
        match = re.search(r'\d+', answer)
        if match and "NONE" not in answer.upper():
            return int(match.group())
    except Exception as e:
        _log_ollama_error("Ошибка при запросе к Ollama", e)
    return None


DESCRIPTION_KEYWORDS = [
    "Описание",
    "описание",
    "Характеристики и описание",
    "О товаре",
    "Описание товара",
    "Характеристики",
    "характеристики"
    "Подробное описание",
]

EXPAND_KEYWORDS = [
    "Показать полностью",
    "Показать целиком",
    "Все характеристики",
    "Показать ещё"
    "Показать еще"
]


def expand_description(page, selector=None):
    """Кликает кнопки 'Показать полностью' и подобные пока они есть.

    Использует page.mouse.click(x, y) — настоящий физический клик мышью,
    который работает на React/Vue и любых JS-фреймворках в отличие от JS el.click().
    """
    for i in range(10):
        found = False
        for kw in EXPAND_KEYWORDS:
            try:
                els = page.get_by_text(kw, exact=True)
                n = els.count()
                if n == 0:
                    continue

                # Один и тот же текст ("Все характеристики") может встречаться
                # дважды: как якорная ссылка <a href="#..."> (просто скроллит
                # к разделу, ничего не раскрывает) и как настоящий раскрыватель
                # на label[for]/button. .first берёт первый по DOM — часто это
                # якорь. Поэтому перебираем все совпадения и выбираем тоггл.
                chosen = None    # индекс настоящего раскрывателя (label/button)
                fallback = None  # запасной не-якорный кандидат
                for idx in range(n):
                    try:
                        info = els.nth(idx).evaluate("""e => {
                            const label = e.closest('label[for]');
                            const btn = e.closest('button');
                            const anchor = e.closest('a');
                            const c = label || btn || anchor || e;
                            const r = c.getBoundingClientRect();
                            const href = anchor ? (anchor.getAttribute('href') || '').trim() : '';
                            return {
                                already: !!e.closest('[data-expand-clicked]'),
                                visible: r.width > 0 && r.height > 0,
                                isToggle: !!(label || btn),
                                isJump: !!anchor && !label && !btn && href.startsWith('#')
                            };
                        }""")
                    except Exception:
                        continue

                    if info['already'] or not info['visible'] or info['isJump']:
                        continue
                    if info['isToggle']:
                        chosen = idx
                        break
                    if fallback is None:
                        fallback = idx

                target_idx = chosen if chosen is not None else fallback
                if target_idx is None:
                    continue

                el = els.nth(target_idx)

                # Прокручиваем элемент в область видимости
                el.scroll_into_view_if_needed(timeout=2000)
                page.wait_for_timeout(200)

                # Берём координаты центра кликабельного предка (label[for] или button/a)
                # и помечаем его как нажатый
                box = el.evaluate("""e => {
                    const c = e.closest('label[for]') || e.closest('button, a') || e;
                    c.setAttribute('data-expand-clicked', '1');
                    const r = c.getBoundingClientRect();
                    return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) };
                }""")

                # Физический клик мышью — работает на любом фреймворке
                page.mouse.click(box['x'], box['y'])
                found = True
                break

            except Exception as e:
                print(f"  Ошибка клика '{kw}': {e}")
                continue

        if not found:
            print(f"  Кнопок 'Показать полностью' больше нет (итераций: {i}).")
            break
        print(f"  Кнопка раскрытия нажата (итерация {i + 1}).")
        page.wait_for_timeout(600)


def strip_heading(text, keyword):
    """Убирает строку с ключевым словом из начала текста."""
    lines = text.split('\n')
    cleaned = [l for l in lines if l.strip().lower() != keyword.lower()]
    return '\n'.join(cleaned).strip()


def llm_is_description(text):
    """Спрашивает LLM: является ли текст описанием товара? Возвращает True/False."""
    stripped = text.strip()
    sentence_count = len(re.findall(r'[.!?][\s\n]', stripped)) + (1 if stripped and stripped[-1] in '.!?' else 0)

    # Явно слишком короткий без предложений → не описание
    if len(stripped) < 80 and sentence_count == 0:
        print(f"  Эвристика: слишком короткий ({len(stripped)} симв.) → не описание")
        return False

    # Много предложений → точно описание, LLM не нужна
    if sentence_count >= 3:
        print(f"  Эвристика: {sentence_count} предложений → описание")
        return True

    # Пограничный случай (1–2 предложения) → спрашиваем LLM
    if not ollama_model_available():
        return False

    prompt = f"""Тебе будет передан текст со страницы товара.

Задача: определить, является ли этот текст описанием товара.

Правила принятия решения:
- Описание товара — связный текст о товаре: что это, как работает, состав, преимущества, польза.
- НЕ описание: список характеристик (ключ: значение), навигационные ссылки, кнопки, заголовки разделов, отзывы, информация о доставке и оплате.

Примеры:
Текст: "Коллаген – незаменимый для организма компонент. Он обеспечивает упругость кожи и здоровье суставов."
Ответ: Да

Текст: "Артикул: 12345. Вес: 200г. Страна производства: Россия."
Ответ: Нет

Текст: "Доставка. В корзину. Купить сейчас. Перейти."
Ответ: Нет

Формат ответа: напиши только «Да» или «Нет».

Текст: {stripped[:500]}
"""
    try:
        resp = requests.post(OLLAMA_URL, json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "keep_alive": OLLAMA_KEEP_ALIVE,
            "options": {"temperature": 0.5}
        }, timeout=OLLAMA_TIMEOUT)
        resp.raise_for_status()
        answer = resp.json()["response"].strip()
        is_desc = answer.lower().startswith("да")
        print(f"  LLM-валидация ({'описание' if is_desc else 'мусор'}): {answer[:200]}")
        return is_desc
    except Exception as e:
        _log_ollama_error("  Ошибка LLM-валидации", e)
        return False


def find_element_by_keyword(page, keyword):
    """Ищет элемент с ключевым словом на странице.
    Возвращает dict: {type: 'heading'|'trigger', selector: str, text: str} или None."""
    return page.evaluate("""(keyword) => {
        function buildSelector(el) {
            if (el.id) return '#' + el.id;
            if (el.className && typeof el.className === 'string') {
                const classes = el.className.trim().split(/\\s+/).filter(c => c.length > 0 && !c.match(/^\\d/));
                const tag = el.tagName.toLowerCase();
                const parent = el.parentElement;
                if (parent) {
                    const idx = [...parent.children].indexOf(el) + 1;
                    // Используем все классы + nth-child для уникальности
                    const clsSel = classes.length > 0 ? '.' + classes.join('.') : '';
                    return tag + clsSel + ':nth-child(' + idx + ')';
                }
                if (classes.length > 0) return tag + '.' + classes.join('.');
            }
            const parent = el.parentElement;
            if (parent) {
                const idx = [...parent.children].indexOf(el) + 1;
                return el.tagName.toLowerCase() + ':nth-child(' + idx + ')';
            }
            return el.tagName.toLowerCase();
        }

        function isProse(text) {
            // Проверяем: есть ли в тексте хотя бы 2 предложения (точки с пробелом/концом)
            const sentences = (text.match(/[.!?]\\s/g) || []).length;
            if (sentences < 1) return false;
            // Не характеристики: нет частых "ключ\\nзначение" паттернов
            const lines = text.split('\\n').filter(l => l.trim());
            const shortLines = lines.filter(l => l.trim().length < 40).length;
            return shortLines / lines.length < 0.6;
        }

        function findProseChild(container) {
            // Ищем внутри контейнера первый элемент с прозаическим текстом
            const candidates = container.querySelectorAll('div, p, section, article');
            for (const ch of candidates) {
                const text = (ch.innerText || '').trim();
                if (text.length < 100) continue;
                if (isProse(text)) {
                    // Проверяем что это не весь контейнер (нужен более узкий элемент)
                    if (ch === container) continue;
                    return ch;
                }
            }
            return null;
        }

        const kw = keyword.toLowerCase();
        const all = document.querySelectorAll('*');

        // Проход 1: ищем заголовки (heading) — безопасно, текст уже на странице
        for (const el of all) {
            if (el.children.length > 3) continue;
            const t = (el.innerText || '').trim();
            if (!t) continue;
            if (!el.offsetParent) continue;

            const tag = el.tagName.toLowerCase();
            const tLower = t.toLowerCase();

            // Для заголовков: точное совпадение или начало текста
            if (['h1','h2','h3','h4','h5'].includes(tag)) {
                const exactMatch = tLower === kw;
                const startsWithKeyword = tLower.startsWith(kw);
                
                if (!exactMatch && !startsWithKeyword) continue;

                // Ищем контейнер с текстом > 200 символов
                let container = el.parentElement;
                for (let i = 0; i < 6; i++) {
                    if (!container) break;
                    if ((container.innerText || '').length > 200) break;
                    container = container.parentElement;
                }
                if (!container) continue;

                // Ищем внутри контейнера первый блок с прозой (не весь контейнер)
                const proseEl = findProseChild(container);
                const target = proseEl || container;
                const rect = target.getBoundingClientRect();
                return {
                    type: 'heading',
                    selector: buildSelector(target),
                    scrollY:    Math.round(rect.top    + window.scrollY),
                    endScrollY: Math.round(rect.bottom + window.scrollY),
                    text: (target.innerText || '').trim()
                };
            }
        }

        // Проход 2: ищем триггеры (кнопки/ссылки) — только если заголовок не найден
        for (const el of all) {
            if (el.children.length > 3) continue;
            const t = (el.innerText || '').trim();
            if (!t) continue;
            if (!el.offsetParent) continue;

            const tag = el.tagName.toLowerCase();
            const cursor = getComputedStyle(el).cursor;
            const role = (el.getAttribute('role') || '').toLowerCase();
            const tLower = t.toLowerCase();
            const keywordInText = tLower.includes(kw);
            const textIsShort = t.length <= 150;

            if (['button', 'a', 'summary'].includes(tag) || cursor === 'pointer' || ['button','tab','link'].includes(role)) {
                if (keywordInText && textIsShort) {
                    return { type: 'trigger', selector: buildSelector(el), text: t };
                }
            }
        }

        return null;
    }""", keyword)


def get_text_after_click(page, keyword="Описание"):
    """После клика ищет текст описания на странице.

    Стратегии (по порядку):
    1. Заголовок h1-h5 с текстом == keyword → берём sibling или родителя
    2. Любой заголовок h1-h5 с keyword в тексте (частичное совпадение)
    3. Фоллбэк: самый большой видимый прозаический блок на странице
    """
    desc = page.evaluate("""(kw) => {
        function isVisible(el) {
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < window.innerHeight * 3;
        }

        function isProse(text) {
            const sentences = (text.match(/[.!?][\\s\\n]/g) || []).length;
            if (sentences < 1) return false;
            const lines = text.split('\\n').filter(l => l.trim());
            const shortLines = lines.filter(l => l.trim().length < 40).length;
            return shortLines / lines.length < 0.6;
        }

        function textFromHeading(h) {
            // Ищем следующий sibling с достаточным текстом
            let sibling = h.nextElementSibling;
            while (sibling) {
                const t = (sibling.innerText || '').trim();
                if (t.length > 100) {
                    h.scrollIntoView({behavior: 'instant', block: 'start'});
                    return t;
                }
                sibling = sibling.nextElementSibling;
            }
            // Фоллбэк — родитель
            const section = h.parentElement;
            if (section) {
                const t = (section.innerText || '').trim();
                if (t.length > 100) {
                    h.scrollIntoView({behavior: 'instant', block: 'start'});
                    return t;
                }
            }
            return null;
        }

        const kwLower = kw.toLowerCase();

        // Проход 1: точное совпадение заголовка
        for (const h of document.querySelectorAll('h1,h2,h3,h4,h5')) {
            const t = (h.innerText || '').trim();
            if (t.toLowerCase() !== kwLower) continue;
            if (!isVisible(h)) continue;
            const text = textFromHeading(h);
            if (text) return { text };
        }

        // Проход 2: частичное совпадение заголовка
        for (const h of document.querySelectorAll('h1,h2,h3,h4,h5')) {
            const t = (h.innerText || '').trim().toLowerCase();
            if (!t.includes(kwLower)) continue;
            if (!isVisible(h)) continue;
            const text = textFromHeading(h);
            if (text) return { text };
        }

        // Проход 3: самый большой видимый прозаический блок
        let bestEl = null, bestLen = 0;
        for (const el of document.querySelectorAll('p, div, section, article')) {
            if (el.children.length > 5) continue;
            if (!isVisible(el)) continue;
            const t = (el.innerText || '').trim();
            if (t.length < 150) continue;
            if (!isProse(t)) continue;
            if (t.length > bestLen) { bestLen = t.length; bestEl = el; }
        }
        if (bestEl) return { text: (bestEl.innerText || '').trim() };

        return null;
    }""", keyword)

    if desc and desc.get("text"):
        return desc
    return None


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


def process_modal_info(page):
    """Ищет описание товара перебором ключевых слов с LLM-валидацией."""
    try:
        clicked_keywords = set()  # не кликать одно и то же дважды

        for keyword in DESCRIPTION_KEYWORDS:
            print(f"\n--- Пробуем ключевое слово: '{keyword}' ---")

            result = find_element_by_keyword(page, keyword)
            if not result:
                print(f"  Элемент '{keyword}' не найден на странице.")
                continue

            print(f"  Найден элемент типа '{result['type']}'")

            if result["type"] == "heading":
                # Описание уже видно на странице
                text = result.get("text", "")
                text_for_llm = strip_heading(text, keyword)
                if len(text_for_llm) < 100:
                    print(f"  Текст слишком короткий ({len(text_for_llm)} символов), пропускаем.")
                    continue
                print(f"  Текст: {text_for_llm[:80]}...")
                if llm_is_description(text_for_llm):
                    print("  LLM: это описание")
                    expand_description(page, selector=result["selector"])
                    # Повторно ищем элемент после раскрытия — берём и текст, и координаты
                    # из одного источника, чтобы не использовать хрупкий старый селектор.
                    page.wait_for_timeout(600)
                    fresh = find_element_by_keyword(page, keyword)
                    if fresh:
                        expanded_text = fresh.get("text") or text
                        end_y = fresh.get("endScrollY") or result.get("endScrollY")
                    else:
                        expanded_text = text
                        end_y = result.get("endScrollY")
                    print(f"  end_y после раскрытия: {end_y}")
                    with open(back_dir / "description.txt", "w", encoding="utf-8", errors="replace") as f:
                        f.write(expanded_text)
                    screenshot_description_and_specs(page, str(back_dir / "description_section.png"), scroll_y=result.get("scrollY"), end_y=end_y)
                    return
                else:
                    print("  LLM: не описание, пробуем дальше.")

            elif result["type"] == "trigger":
                if keyword in clicked_keywords:
                    continue
                clicked_keywords.add(keyword)

                print(f"  Кликаем триггер...")
                el = page.locator(result["selector"]).first
                if el.count() == 0:
                    print("  Триггер не найден в DOM.")
                    continue
                try:
                    el.scroll_into_view_if_needed(timeout=3000)
                    page.wait_for_timeout(500)
                    el.click(force=True, timeout=5000)
                except Exception as click_err:
                    print(f"  Ошибка клика: {click_err}")
                    continue
                page.wait_for_timeout(2000)

                after = get_text_after_click(page, keyword)
                if not after or not after.get("text"):
                    print("  После клика текст не найден.")
                    continue

                text = after["text"]
                text_for_llm = strip_heading(text, keyword)
                if len(text_for_llm) < 100:
                    print(f"  Текст слишком короткий ({len(text_for_llm)} символов), пропускаем.")
                    continue

                print(f"  Текст после клика: {text_for_llm[:80]}...")
                if llm_is_description(text_for_llm):
                    print("  LLM: это описание")
                    expand_description(page)
                    # Перечитываем текст после раскрытия
                    after_expanded = get_text_after_click(page, keyword)
                    expanded_text = after_expanded.get("text") if after_expanded else None
                    with open(back_dir / "description.txt", "w", encoding="utf-8", errors="replace") as f:
                        f.write(expanded_text or text)
                    screenshot_description_and_specs(page, str(back_dir / "description_section.png"))
                    return
                else:
                    print("  LLM: не описание, пробуем дальше.")
                    # Закрываем модалку если открылась
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(500)

        # Фоллбэк: текстовые блоки + эвристика + LLM
        print("\n--- Фоллбэк: поиск по текстовым блокам ---")
        blocks = extract_text_blocks(page)
        desc_blocks = filter_description_blocks(blocks)

        if not desc_blocks:
            print("Описание не найдено.")
            page.screenshot(path="description_section.png", full_page=True)
            return

        if len(desc_blocks) == 1:
            block = desc_blocks[0]
        else:
            for i, b in enumerate(desc_blocks):
                b["index"] = i
            chosen = ask_llm_to_pick(desc_blocks)
            block = desc_blocks[chosen] if chosen is not None and chosen < len(desc_blocks) else desc_blocks[0]

        text = block["preview"]
        if llm_is_description(text):
            with open(back_dir / "description.txt", "w", encoding="utf-8", errors="replace") as f:
                f.write(text)
            screenshot_description_and_specs(page, str(back_dir / "description_section.png"))
        else:
            print("Описание не найдено даже в фоллбэке.")
            page.screenshot(path="description_section.png", full_page=True)

    except Exception as e:
        print(f"Ошибка при обработке описания: {e}")


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

        # Выполняем вторую часть (модальное окно)
        process_modal_info(page)
    """
        current_dir = os.path.dirname(os.path.abspath(__file__))
        compiler_path = os.path.join(current_dir, 'compiler.py')
        comp_res = subprocess.run(['python', compiler_path], capture_output=True, text=True)
        print(comp_res.stdout)
    """