import subprocess
import time
import os
import re
import json
import textwrap
import requests
from io import BytesIO
from datetime import datetime
from playwright.sync_api import sync_playwright
from PIL import Image, ImageDraw, ImageFont

#from llama_cpp import Llama
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:3b"

#llm = Llama(model_path="./qwen2.5-3b.gguf")
# --- НАСТРОЙКИ И ПУТИ ---


def open_site(p, url):
    """Запуск браузера и переход на страницу (поддерживает Linux и Windows)."""
    DEBUG_PORT = "9222"

    # Определяем путь к браузеру и папку пользовательских данных в зависимости от ОС
    if os.name == "posix":  # Linux / macOS
        chrome_path = "chromium"  # предполагается, что chromium установлен в PATH
        user_data_dir = "/tmp/playwright"
    elif os.name == "nt":  # Windows
        possible_paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ]
        chrome_path = None
        for path in possible_paths:
            if os.path.exists(path):
                chrome_path = path
                break
        if chrome_path is None:
            raise FileNotFoundError("Chrome не найден. Проверьте пути в possible_paths.")
        user_data_dir = r"C:\Temp\chrome-debug"
    else:
        raise OSError(f"Unsupported OS: {os.name}")

    # Запускаем браузер с отладочным портом
    subprocess.Popen([
        chrome_path,
        f"--remote-debugging-port={DEBUG_PORT}",
        f"--user-data-dir={user_data_dir}",
        "--start-maximized",
        "--force-device-scale-factor=1"
    ])

    print("Ожидание запуска браузера...")
    time.sleep(5)

    # Подключаемся к браузеру через CDP
    browser = p.chromium.connect_over_cdp(f"http://localhost:{DEBUG_PORT}")
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    page = context.new_page()

    # Устанавливаем размер окна для стандартного скриншота
    page.set_viewport_size({"width": 1920, "height": 1080})

    print(f"Переход по ссылке: {url}")
    page.goto(url, wait_until="domcontentloaded")
    return browser, page


def save_product_info(page, url):
    """Извлекает название товара и сохраняет в product_name.txt."""
    selector = 'h2.productTitle--lfc4o'
    try:
        element = page.wait_for_selector(selector, timeout=10000)
        product_name = element.inner_text().strip()
    except Exception:
        product_name = "Название не найдено"

    with open("product_name.txt", "w", encoding="utf-8") as f:
        f.write(f"{product_name}")
    print(f"Инфо сохранено: {product_name}")


def make_main_screenshot(page):
    """Делает общий скриншот и накладывает дату/время."""
    screenshot_bytes = page.screenshot(full_page=False)
    image = Image.open(BytesIO(screenshot_bytes))
    draw = ImageDraw.Draw(image)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        # Попытка загрузить шрифт (может потребоваться путь к .ttf в Linux)
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 40)
    except:
        font = ImageFont.load_default()

    # Рисуем текст в левом нижнем углу
    draw.text((20, image.height - 60), timestamp, fill="red", font=font)

    path = "final_screenshot.png"
    image.save(path)
    print(f"Общий скриншот сохранен: {path}")


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
            "options": {"temperature": 0.1}
        }, timeout=120)
        resp.raise_for_status()
        answer = resp.json()["response"].strip()
        print(f"Ответ LLM: {answer}")

        # Ищем число в ответе
        match = re.search(r'\d+', answer)
        if match and "NONE" not in answer.upper():
            return int(match.group())
    except Exception as e:
        print(f"Ошибка при запросе к Ollama: {e}")
    return None


DESCRIPTION_KEYWORDS = [
    "Описание",
    "Характеристики и описание",
    "О товаре",
    "Описание товара",
    "Характеристики",
]

# Ключевые слова для поиска разделов при скриншоте (в нижнем регистре)
SPEC_KEYWORDS_SCREENSHOT = [
    "характеристики",
    "характеристики и описание",
    "свойства",
    "параметры",
]
DESC_KEYWORDS_SCREENSHOT = [
    "описание",
    "описание товара",
    "о товаре",
]


def strip_heading(text, keyword):
    """Убирает строку с ключевым словом из начала текста."""
    lines = text.split('\n')
    cleaned = [l for l in lines if l.strip().lower() != keyword.lower()]
    return '\n'.join(cleaned).strip()


def llm_is_description(text):
    """Спрашивает LLM: является ли текст описанием товара? Возвращает True/False."""
    prompt = f"""You are a helpful assistant that classifies web page content.
    Analyze the text below and decide if it is a product DESCRIPTION (prose story about the item or its components) or just technical data.

    A product DESCRIPTION is a prose story either about the product, or about its components, in full sentences.
    A text is NOT a description if it is just a list of specs, technical data, or site navigation.
    EXAMPLES:
    - "Коллаген – незаменимый для организма компонент, который требует постоянного восполнения с помощью пищи. Согласно научным данным, количество коллагена в организме составляет около 6% от общей массы тела. Вещество присутствует почти во всех тканях и оказывает комплексное воздействие на работу организма, всех систем и здоровья в целом." -> YES
    - "Беспроводная док-станция Wireless Charging Dock for Kindle Paperwhite Signature Edition. При помещении ридера на док-станцию, он заряжается автоматически. Зарядка до 100% осуществляется за 2 часа. Ридер можно заряжать, не вынимая из чехла" -> YES
    - "Вес: 500г. Срок годности: 24 месяца. Сделано в РФ." -> NO
    - "Доставка завтра. В корзину. Описание. Характеристики." -> NO

    Text: {text[:1000]}

    Is this a product description?"""
    # Answer with only one word: YES or NO.
    try:
        resp = requests.post(OLLAMA_URL, json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.0}
        }, timeout=60)
        resp.raise_for_status()
        answer = resp.json()["response"].strip().upper()
        print(f"  LLM-валидация: {answer[:100]}")
        return answer.startswith("YES")
    except Exception as e:
        print(f"  Ошибка LLM-валидации: {e}")
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
                if (proseEl) {
                    return {
                        type: 'heading',
                        selector: buildSelector(proseEl),
                        text: (proseEl.innerText || '').trim()
                    };
                }

                // Фоллбэк: весь контейнер
                return {
                    type: 'heading',
                    selector: buildSelector(container),
                    text: (container.innerText || '').trim()
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


def get_text_after_click(page):
    """После клика ищет заголовок 'Описание' на странице и берёт текст после него."""
    # Ищем любой ВИДИМЫЙ заголовок "Описание" на странице напрямую,
    # без привязки к типу контейнера (модалка/panel/drawer — неважно)
    desc = page.evaluate("""() => {
        function buildSelector(el) {
            if (el.id) return '#' + el.id;
            if (el.className && typeof el.className === 'string') {
                const classes = el.className.trim().split(/\\s+/).filter(c => c.length > 0);
                const tag = el.tagName.toLowerCase();
                const parent = el.parentElement;
                if (parent) {
                    const idx = [...parent.children].indexOf(el) + 1;
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

        for (const h of document.querySelectorAll('h1,h2,h3,h4,h5')) {
            if (!(h.innerText||'').trim().match(/^Описание$/i)) continue;
            if (!h.offsetParent) continue;  // только видимые

            // Пробуем следующий sibling с достаточным текстом
            let sibling = h.nextElementSibling;
            while (sibling) {
                const text = (sibling.innerText || '').trim();
                if (text.length > 100) {
                    h.scrollIntoView({behavior:'instant', block:'start'});
                    return { selector: buildSelector(sibling), text: text };
                }
                sibling = sibling.nextElementSibling;
            }

            // Фоллбэк — родительский контейнер
            const section = h.parentElement;
            if (section && (section.innerText||'').trim().length > 100) {
                h.scrollIntoView({behavior:'instant', block:'start'});
                return { selector: buildSelector(section), text: (section.innerText||'').trim() };
            }
        }
        return null;
    }""")
    if desc and desc.get("text"):
        return desc

    return None


def render_text_as_image(text, path, img_width=900):
    """Рендерит текст описания в чистый PNG через PIL."""
    # Пробуем загрузить нормальный шрифт
    font_size = 18
    font_small = 14
    try:
        font_title = ImageFont.truetype("arial.ttf", font_size + 4)
        font_body  = ImageFont.truetype("arial.ttf", font_size)
    except Exception:
        try:
            # Linux-путь
            font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size + 4)
            font_body  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font_size)
        except Exception:
            font_title = ImageFont.load_default()
            font_body  = font_title

    padding   = 30
    line_h    = font_size + 6
    text_width = img_width - padding * 2
    # Примерно 1 символ ~ font_size * 0.55 px
    chars_per_line = max(40, int(text_width / (font_size * 0.55)))

    lines_out = []
    for para in text.split('\n'):
        para = para.strip()
        if not para:
            lines_out.append(('', None))
            continue
        wrapped = textwrap.wrap(para, width=chars_per_line)
        for i, line in enumerate(wrapped):
            font = font_title if (i == 0 and len(para) < 80) else font_body
            lines_out.append((line, font))
        lines_out.append(('', None))

    height = padding * 2 + len(lines_out) * line_h
    img = Image.new('RGB', (img_width, max(height, 200)), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    y = padding
    for line, font in lines_out:
        if line:
            draw.text((padding, y), line, fill=(20, 20, 20), font=font or font_body)
        y += line_h

    img.save(path)
    print(f"Изображение описания сохранено в {path} ({img_width}x{height}px)")
    return True


def screenshot_container(page, selector, path):
    """Извлекает текст из элемента и рендерит его как PNG."""
    el = page.locator(selector).first
    if el.count() == 0 or not el.is_visible():
        return False

    el.scroll_into_view_if_needed()
    page.wait_for_timeout(300)

    text = el.inner_text().strip()
    if not text:
        return False

    with open("description.txt", "w", encoding="utf-8") as f:
        f.write(text)

    return render_text_as_image(text, path)


def save_description(text, path="description_section.png"):
    """Сохраняет текст в description.txt и рендерит PNG."""
    with open("description.txt", "w", encoding="utf-8") as f:
        f.write(text)
    render_text_as_image(text, path)
    print(f"Описание сохранено ({len(text)} символов)")


def screenshot_full_description(page, path="description_section.png"):
    """Снимает полное описание товара.
    - Основная страница (Ozon и др.): full_page=True + кроп по координатам контейнера.
      Не зависит от sticky-хедеров и скроллинга вообще.
    - Панель/модалка (WB и др.): scroll+stitch viewport-скриншотов."""

    # Определяем: панель это или основная страница, и собираем координаты
    info = page.evaluate("""() => {
        function getScrollContainer(el) {
            let cur = el.parentElement;
            while (cur && cur !== document.body) {
                const oy = getComputedStyle(cur).overflowY;
                if ((oy === 'auto' || oy === 'scroll') && cur.scrollHeight > cur.clientHeight)
                    return cur;
                cur = cur.parentElement;
            }
            return null;
        }

        for (const h of document.querySelectorAll('h1,h2,h3,h4,h5')) {
            if (!(h.innerText || '').trim().match(/^Описание$/i)) continue;
            if (!h.offsetParent) continue;

            const container = getScrollContainer(h);
            if (container) {
                // Панель — scroll+stitch
                container.scrollTop = Math.max(0, h.offsetTop - 16);
                return { mode: 'panel' };
            } else {
                // Основная страница — берём координаты контейнера в документе
                // Ищем родительский блок с полным текстом описания
                let block = h.parentElement;
                while (block && block !== document.body) {
                    if (block.scrollHeight > 300) break;
                    block = block.parentElement;
                }
                const r = block.getBoundingClientRect();
                return {
                    mode: 'fullpage',
                    x: Math.max(0, Math.round(r.left)),
                    // Абсолютная Y в документе (не в viewport)
                    y: Math.max(0, Math.round(r.top + window.scrollY)),
                    width: Math.round(r.width),
                    height: Math.round(block.scrollHeight)
                };
            }
        }
        return null;
    }""")

    if not info:
        page.screenshot(path=path)
        print("screenshot_full_description: заголовок 'Описание' не найден, fallback.")
        return False

    # === Режим 1: основная страница — full_page + кроп ===
    if info["mode"] == "fullpage":
        full_bytes = page.screenshot(full_page=True)
        img = Image.open(BytesIO(full_bytes))
        x, y, w, h = info["x"], info["y"], info["width"], info["height"]
        # Защита от выхода за пределы изображения
        x2 = min(x + w, img.width)
        y2 = min(y + h, img.height)
        cropped = img.crop((x, y, x2, y2))
        cropped.save(path)
        print(f"Скриншот описания сохранён: {path} (full_page crop, {cropped.height}px)")
        return True

    # === Режим 2: панель — scroll + stitch ===
    OVERLAP = 100
    vp = page.viewport_size or {"width": 1280, "height": 800}
    vp_w, vp_h = vp["width"], vp["height"]
    step = vp_h - OVERLAP

    page.wait_for_timeout(300)
    frames = []

    for _ in range(20):
        frame_bytes = page.screenshot()
        frames.append(Image.open(BytesIO(frame_bytes)).copy())

        end_visible = page.evaluate("""() => {
            for (const h of document.querySelectorAll('h1,h2,h3,h4,h5')) {
                if (!(h.innerText || '').trim().match(/^Описание$/i)) continue;
                if (!h.offsetParent) continue;
                let lastEl = null, el = h.nextElementSibling;
                while (el) {
                    if ((el.innerText || '').trim().length > 0) lastEl = el;
                    el = el.nextElementSibling;
                }
                if (!lastEl) return true;
                return lastEl.getBoundingClientRect().bottom <= window.innerHeight + 4;
            }
            return true;
        }""")
        if end_visible:
            break

        scrolled = page.evaluate("""(step) => {
            function getScrollContainer(el) {
                let cur = el.parentElement;
                while (cur && cur !== document.body) {
                    const oy = getComputedStyle(cur).overflowY;
                    if ((oy === 'auto' || oy === 'scroll') && cur.scrollHeight > cur.clientHeight)
                        return cur;
                    cur = cur.parentElement;
                }
                return null;
            }
            for (const h of document.querySelectorAll('h1,h2,h3,h4,h5')) {
                if (!(h.innerText || '').trim().match(/^Описание$/i)) continue;
                if (!h.offsetParent) continue;
                const c = getScrollContainer(h);
                if (c) { const b = c.scrollTop; c.scrollTop += step; return c.scrollTop !== b; }
                const b = window.scrollY; window.scrollBy(0, step); return window.scrollY !== b;
            }
            return false;
        }""", step)
        if not scrolled:
            break
        page.wait_for_timeout(200)

    if len(frames) == 1:
        frames[0].save(path)
        print(f"Скриншот описания сохранён: {path} (1 кадр)")
        return True

    total_h = vp_h + (len(frames) - 1) * step
    result = Image.new("RGB", (vp_w, total_h), (255, 255, 255))
    result.paste(frames[0], (0, 0))
    for i, frame in enumerate(frames[1:], 1):
        result.paste(frame, (0, i * step))
    result.save(path)
    print(f"Скриншот описания сохранён: {path} ({len(frames)} кадров, {total_h}px)")
    return True


def screenshot_description_and_specs(page, path="description_section.png"):
    """Снимает скриншот, охватывающий оба раздела: характеристики и описание.
    В txt по-прежнему сохраняется только описание — функция отвечает исключительно за скриншот.

    - fullpage (Ozon и др.): ищет общий контейнер-предок обоих заголовков → full_page + кроп.
    - panel (WB и др.): scroll+stitch, начиная с заголовка характеристик.
    - Если характеристики не найдены — ведёт себя как screenshot_full_description.
    """
    info = page.evaluate("""([specKws, descKws]) => {
        function getScrollContainer(el) {
            let cur = el.parentElement;
            while (cur && cur !== document.body) {
                const oy = getComputedStyle(cur).overflowY;
                if ((oy === 'auto' || oy === 'scroll') && cur.scrollHeight > cur.clientHeight)
                    return cur;
                cur = cur.parentElement;
            }
            return null;
        }

        function findHeading(keywords) {
            for (const h of document.querySelectorAll('h1,h2,h3,h4,h5')) {
                if (!h.offsetParent) continue;
                const t = (h.innerText || '').trim().toLowerCase();
                for (const kw of keywords) {
                    if (t === kw || t.startsWith(kw)) return h;
                }
            }
            return null;
        }

        const specH = findHeading(specKws);
        const descH = findHeading(descKws);
        const anchorH = specH || descH;
        if (!anchorH) return null;

        const container = getScrollContainer(anchorH);
        if (container) {
            // Panel mode: прокручиваем к началу раздела характеристик
            container.scrollTop = Math.max(0, anchorH.offsetTop - 16);
            return { mode: 'panel' };
        }

        // Fullpage mode: ищем общий контейнер-предок обоих заголовков
        if (specH && descH) {
            let block = specH.parentElement;
            while (block && block !== document.body) {
                if (block.contains(descH) && block.scrollHeight > 300) break;
                block = block.parentElement;
            }
            if (block && block !== document.body) {
                const r = block.getBoundingClientRect();
                return {
                    mode: 'fullpage',
                    x: Math.max(0, Math.round(r.left)),
                    y: Math.max(0, Math.round(r.top + window.scrollY)),
                    width: Math.round(r.width),
                    height: Math.round(block.scrollHeight)
                };
            }
        }

        // Fallback: берём контейнер якорного заголовка
        let block = anchorH.parentElement;
        while (block && block !== document.body) {
            if (block.scrollHeight > 300) break;
            block = block.parentElement;
        }
        if (!block || block === document.body) return null;
        const r = block.getBoundingClientRect();
        return {
            mode: 'fullpage',
            x: Math.max(0, Math.round(r.left)),
            y: Math.max(0, Math.round(r.top + window.scrollY)),
            width: Math.round(r.width),
            height: Math.round(block.scrollHeight)
        };
    }""", [SPEC_KEYWORDS_SCREENSHOT, DESC_KEYWORDS_SCREENSHOT])

    if not info:
        page.screenshot(path=path)
        print("screenshot_description_and_specs: разделы не найдены, fallback.")
        return False

    # === Режим 1: основная страница — full_page + кроп ===
    if info["mode"] == "fullpage":
        full_bytes = page.screenshot(full_page=True)
        img = Image.open(BytesIO(full_bytes))
        x, y, w, h = info["x"], info["y"], info["width"], info["height"]
        x2 = min(x + w, img.width)
        y2 = min(y + h, img.height)
        cropped = img.crop((x, y, x2, y2))
        cropped.save(path)
        print(f"Скриншот характеристик+описания сохранён: {path} (full_page crop, {cropped.height}px)")
        return True

    # === Режим 2: панель — scroll + stitch до конца описания ===
    OVERLAP = 100
    vp = page.viewport_size or {"width": 1280, "height": 800}
    vp_w, vp_h = vp["width"], vp["height"]
    step = vp_h - OVERLAP

    page.wait_for_timeout(300)
    frames = []

    for _ in range(20):
        frame_bytes = page.screenshot()
        frames.append(Image.open(BytesIO(frame_bytes)).copy())

        end_visible = page.evaluate("""(descKws) => {
            for (const h of document.querySelectorAll('h1,h2,h3,h4,h5')) {
                if (!h.offsetParent) continue;
                const t = (h.innerText || '').trim().toLowerCase();
                if (!descKws.some(kw => t === kw || t.startsWith(kw))) continue;
                let lastEl = null, el = h.nextElementSibling;
                while (el) {
                    if ((el.innerText || '').trim().length > 0) lastEl = el;
                    el = el.nextElementSibling;
                }
                if (!lastEl) return true;
                return lastEl.getBoundingClientRect().bottom <= window.innerHeight + 4;
            }
            return true;
        }""", DESC_KEYWORDS_SCREENSHOT)
        if end_visible:
            break

        scrolled = page.evaluate("""([step, anchorKws]) => {
            function getScrollContainer(el) {
                let cur = el.parentElement;
                while (cur && cur !== document.body) {
                    const oy = getComputedStyle(cur).overflowY;
                    if ((oy === 'auto' || oy === 'scroll') && cur.scrollHeight > cur.clientHeight)
                        return cur;
                    cur = cur.parentElement;
                }
                return null;
            }
            function findHeading(kws) {
                for (const h of document.querySelectorAll('h1,h2,h3,h4,h5')) {
                    if (!h.offsetParent) continue;
                    const t = (h.innerText || '').trim().toLowerCase();
                    for (const kw of kws) {
                        if (t === kw || t.startsWith(kw)) return h;
                    }
                }
                return null;
            }
            const h = findHeading(anchorKws);
            if (!h) return false;
            const c = getScrollContainer(h);
            if (c) { const b = c.scrollTop; c.scrollTop += step; return c.scrollTop !== b; }
            const b = window.scrollY; window.scrollBy(0, step); return window.scrollY !== b;
        }""", [step, SPEC_KEYWORDS_SCREENSHOT])
        if not scrolled:
            break
        page.wait_for_timeout(200)

    if len(frames) == 1:
        frames[0].save(path)
        print(f"Скриншот характеристик+описания сохранён: {path} (1 кадр)")
        return True

    total_h = vp_h + (len(frames) - 1) * step
    result = Image.new("RGB", (vp_w, total_h), (255, 255, 255))
    result.paste(frames[0], (0, 0))
    for i, frame in enumerate(frames[1:], 1):
        result.paste(frame, (0, i * step))
    result.save(path)
    print(f"Скриншот характеристик+описания сохранён: {path} ({len(frames)} кадров, {total_h}px)")
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
                    print("  LLM: это описание ✓")
                    with open("description.txt", "w", encoding="utf-8") as f:
                        f.write(text)
                    screenshot_description_and_specs(page, "description_section.png")
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

                after = get_text_after_click(page)
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
                    print("  LLM: это описание ✓")
                    with open("description.txt", "w", encoding="utf-8") as f:
                        f.write(text)
                    # Закрываем модалку — текст уже сохранён, скриншот делаем с основной страницы,
                    # чтобы захватить и характеристики, и описание вместе
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(700)
                    screenshot_description_and_specs(page, "description_section.png")
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
            with open("description.txt", "w", encoding="utf-8") as f:
                f.write(text)
            screenshot_description_and_specs(page, "description_section.png")
        else:
            print("Описание не найдено даже в фоллбэке.")
            page.screenshot(path="description_section.png", full_page=True)

    except Exception as e:
        print(f"Ошибка при обработке описания: {e}")


# --- ЗАПУСК ---
if __name__ == "__main__":
    target_url = input("Вставьте ссылку на товар: ").strip()
    with sync_playwright() as p:
        browser, page = open_site(p, target_url)

        try:
            # Выполняем первую часть задач
            save_product_info(page, target_url)
            make_main_screenshot(page)

            # Выполняем вторую часть (модальное окно)
            process_modal_info(page)

        finally:
            print("\nРабота завершена.")
            input("Нажмите Enter, чтобы закрыть браузер...")
            browser.close()