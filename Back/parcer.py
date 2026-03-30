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

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2:3b"

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
            if (el.children.length > 5) continue;

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

            if (blocks.length >= 30) break;
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


def find_description_by_heading(page):
    """Ищет заголовок 'Описание' на странице и возвращает его родительский контейнер."""
    result = page.evaluate("""() => {
        const headings = document.querySelectorAll('h1, h2, h3, h4');
        for (const h of headings) {
            if (h.innerText?.trim().match(/^Описание/i)) {
                // Ищем родительский контейнер с достаточным количеством текста
                let el = h.parentElement;
                for (let i = 0; i < 6; i++) {
                    if (!el) break;
                    if (el.innerText?.length > 300) {
                        // Строим уникальный селектор
                        if (el.id) return '#' + el.id;
                        if (el.className) {
                            const cls = el.className.trim().split(/\\s+/).filter(c => c.length > 0)[0];
                            if (cls) return el.tagName.toLowerCase() + '.' + cls;
                        }
                        // По позиции среди родителей
                        const parent = el.parentElement;
                        if (parent) {
                            const idx = [...parent.children].indexOf(el) + 1;
                            return el.tagName.toLowerCase() + ':nth-child(' + idx + ')';
                        }
                    }
                    el = el.parentElement;
                }
            }
        }
        return null;
    }""")
    return result


def click_description_trigger(page):
    """Ищет кликабельный элемент, ведущий к описанию, и кликает его."""
    # Ищем любой элемент с текстом про описание — включая div с cursor:pointer
    triggers = page.locator("text=/Перейти к описани|Описание товара|Характеристики и описание|О товаре/i")
    for i in range(min(triggers.count(), 5)):
        btn = triggers.nth(i)
        if not btn.is_visible():
            continue
        cursor = btn.evaluate("el => getComputedStyle(el).cursor")
        tag = btn.evaluate("el => el.tagName.toLowerCase()")
        if tag in ("button", "a", "summary") or cursor == "pointer":
            print(f"Найден триггер описания ({tag}, cursor={cursor}) — кликаем...")
            btn.scroll_into_view_if_needed()
            btn.click()
            page.wait_for_timeout(2000)
            return True
    return False


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


def process_modal_info(page):
    """Универсальный поиск описания товара."""
    try:
        # Шаг 1: ищем заголовок "Описание" прямо на странице (описание уже в DOM)
        print("Ищем заголовок 'Описание' на странице...")
        selector = find_description_by_heading(page)
        if selector:
            print(f"Найден контейнер описания: {selector}")
            if screenshot_container(page, selector, "description_section.png"):
                return

        # Шаг 2: ищем триггер-кнопку и кликаем (описание может быть скрыто)
        print("Ищем кнопку/ссылку для перехода к описанию...")
        clicked = click_description_trigger(page)

        if clicked:
            # После клика снова ищем заголовок
            selector = find_description_by_heading(page)
            if selector:
                print(f"После клика найден контейнер: {selector}")
                if screenshot_container(page, selector, "description_section.png"):
                    return

            # Может быть модалка
            modal = page.locator("div[role='dialog'], [class*='modal'], [class*='popup']").first
            if modal.count() > 0 and modal.is_visible():
                # Ищем секцию с описанием внутри модалки (WB-паттерн: h3 "Описание" → parent section)
                desc_section = page.evaluate("""() => {
                    const headings = document.querySelectorAll('h2, h3, h4');
                    for (const h of headings) {
                        if (h.innerText?.trim().match(/^Описание$/i)) {
                            const section = h.parentElement;
                            // Скроллим секцию в видимую область внутри скролл-контейнера
                            h.scrollIntoView({behavior: 'instant', block: 'start'});
                            if (section.id) return '#' + section.id;
                            if (section.className) {
                                const cls = section.className.trim().split(/\\s+/)[0];
                                if (cls) return section.tagName.toLowerCase() + '.' + cls;
                            }
                            // Если нет класса — строим путь через родителя
                            const parent = section.parentElement;
                            if (parent) {
                                const idx = [...parent.children].indexOf(section) + 1;
                                return section.tagName.toLowerCase() + ':nth-child(' + idx + ')';
                            }
                        }
                    }
                    return null;
                }""")

                if desc_section:
                    print(f"Найдена секция описания внутри модалки: {desc_section}")
                    page.wait_for_timeout(500)
                    if screenshot_container(page, desc_section, "description_section.png"):
                        return

                # Фоллбэк: берём текст всей модалки и рендерим
                text = modal.inner_text().strip()
                if text:
                    with open("description.txt", "w", encoding="utf-8") as f:
                        f.write(text)
                    render_text_as_image(text, "only_modal.png")
                return

        # Шаг 3: фоллбэк — извлекаем текстовые блоки и спрашиваем LLM
        print("Извлекаем текстовые блоки со страницы (фоллбэк)...")
        blocks = extract_text_blocks(page)
        print(f"Найдено {len(blocks)} текстовых блоков")

        if not blocks:
            print("Текстовые блоки не найдены.")
            page.screenshot(path="description_section.png", full_page=True)
            return

        print("Фильтруем блоки эвристикой...")
        desc_blocks = filter_description_blocks(blocks)

        if not desc_blocks:
            print("Ни один блок не похож на описание. Сохраняем полный скриншот.")
            page.screenshot(path="description_section.png", full_page=True)
            return

        if len(desc_blocks) == 1:
            block = desc_blocks[0]
            print(f"Один подходящий блок — берём без LLM: {block['preview'][:80]}...")
        else:
            print(f"Осталось {len(desc_blocks)} кандидатов, спрашиваем LLM...")
            for i, b in enumerate(desc_blocks):
                b["index"] = i
            chosen = ask_llm_to_pick(desc_blocks)
            if chosen is not None and chosen < len(desc_blocks):
                block = desc_blocks[chosen]
                print(f"LLM выбрала блок [{chosen}]: {block['preview'][:80]}...")
            else:
                block = desc_blocks[0]
                print(f"LLM не определилась, берём лучший по score: {block['preview'][:80]}...")

        element = page.locator(block["selector"]).first
        if element.count() > 0 and element.is_visible():
            element.scroll_into_view_if_needed()
            page.wait_for_timeout(500)
            element.screenshot(path="description_section.png")
            text = element.inner_text()
            if text.strip():
                with open("description.txt", "w", encoding="utf-8") as f:
                    f.write(text.strip())
                print("Текст описания сохранен в description.txt")
            print("Скриншот описания сохранен в description_section.png")
        else:
            print(f"Элемент '{block['selector']}' не найден. Сохраняем полный скриншот.")
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