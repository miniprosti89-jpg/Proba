import subprocess
import time
import os
import re
import sys
import textwrap
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


PICK_OVERLAY_JS = r"""
(instruction) => {
    const MARK = 'data-pd-overlay';
    window.__pdConfirmed = false;
    window.__pdResult = null;
    window.__pdPickedEl = null;
    window.__pdHoverEl = null;

    // Отдельный AbortController на каждый вызов — гарантирует, что клик-перехватчик
    // (capture-phase, блокирующий обычные клики по странице в режиме выбора)
    // снимается вместе с оверлеем и не остаётся висеть на следующем шаге.
    const controller = new AbortController();
    window.__pdAbort = controller;
    const { signal } = controller;

    const panel = document.createElement('div');
    panel.setAttribute(MARK, '1');
    panel.style.cssText = `
        position: fixed; top: 12px; right: 12px; z-index: 2147483647;
        background: #222; color: #fff; padding: 10px 14px; border-radius: 8px;
        font: 14px/1.4 sans-serif; box-shadow: 0 2px 10px rgba(0,0,0,.4);
        max-width: 320px;
    `;
    panel.innerHTML = `
        <div style="margin-bottom:8px;">${instruction}</div>
        <button id="__pd_toggle" style="margin-right:6px;padding:6px 10px;cursor:pointer;background:#fff;color:#111;border:1px solid #999;border-radius:4px;">Включить выбор</button>
        <button id="__pd_confirm" style="padding:6px 10px;cursor:pointer;background:#2e7d32;color:#fff;border:1px solid #1b5e20;border-radius:4px;">Подтвердить</button>
    `;
    document.body.appendChild(panel);

    const toggleBtn = panel.querySelector('#__pd_toggle');
    const confirmBtn = panel.querySelector('#__pd_confirm');

    let pickMode = false;

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
        toggleBtn.textContent = on ? 'Режим выбора включён (клик по блоку)' : 'Включить выбор';
        toggleBtn.style.background = on ? '#c62828' : '#fff';
        toggleBtn.style.color = on ? '#fff' : '#111';
    }

    toggleBtn.addEventListener('click', () => setPickMode(!pickMode), { signal });

    document.addEventListener('mouseover', (e) => {
        if (!pickMode || isOverlay(e.target)) return;
        if (window.__pdHoverEl && window.__pdHoverEl !== window.__pdPickedEl) setOutline(window.__pdHoverEl, null);
        window.__pdHoverEl = e.target;
        if (window.__pdHoverEl !== window.__pdPickedEl) setOutline(window.__pdHoverEl, 'orange');
    }, { capture: true, signal });

    document.addEventListener('click', (e) => {
        if (!pickMode || isOverlay(e.target)) return;
        e.preventDefault();
        e.stopPropagation();
        if (window.__pdPickedEl && window.__pdPickedEl !== e.target) setOutline(window.__pdPickedEl, null);
        window.__pdPickedEl = e.target;
        setOutline(window.__pdPickedEl, 'red');
    }, { capture: true, signal });

    // "Подтвердить" работает и без выбранного элемента — на странице может
    // не быть нужного блока (например, описания).
    confirmBtn.addEventListener('click', () => {
        setPickMode(false);
        const pickedEl = window.__pdPickedEl;
        if (pickedEl) {
            const rect = pickedEl.getBoundingClientRect();
            window.__pdResult = {
                text: (pickedEl.innerText || '').trim(),
                scrollY: Math.round(rect.top + window.scrollY),
                endScrollY: Math.round(rect.bottom + window.scrollY),
            };
        } else {
            window.__pdResult = null;
        }
        window.__pdConfirmed = true;
    }, { signal });
}
"""

PICK_CLEANUP_JS = r"""
() => {
    if (window.__pdAbort) {
        window.__pdAbort.abort();
        window.__pdAbort = null;
    }
    // Снимаем подсветку с выбранного/наведённого элемента — иначе рамка
    // останется на реальном элементе страницы и попадёт на скриншоты.
    for (const el of [window.__pdPickedEl, window.__pdHoverEl]) {
        if (el) {
            el.style.outline = '';
            el.style.outlineOffset = '';
        }
    }
    window.__pdPickedEl = null;
    window.__pdHoverEl = null;
    document.querySelectorAll('[data-pd-overlay]').forEach(el => el.remove());
    document.body.style.cursor = '';
}
"""


def pick_element_manually(page, instruction):
    """Показывает оверлей в браузере и ждёт, пока человек кликнет на нужный
    блок и нажмёт "Подтвердить" (или подтвердит без выбора, если блока нет).

    Возвращает {text, scrollY, endScrollY} выбранного элемента, либо None.
    """
    print(f"\n--- Ожидание ручного выбора: {instruction} ---")
    page.evaluate(PICK_OVERLAY_JS, instruction)
    page.wait_for_function("window.__pdConfirmed === true", timeout=0)
    result = page.evaluate("window.__pdResult")
    page.evaluate(PICK_CLEANUP_JS)
    return result


def pick_product_name_manually(page):
    """Человек кликает на название товара в открытом браузере. Сохраняет в product_name.txt."""
    result = pick_element_manually(
        page,
        'Кликните на название товара, затем нажмите «Подтвердить». '
        'Если названия нет — нажмите «Подтвердить», ничего не выбирая.'
    )
    name = result["text"] if result else "Название не найдено"
    print(f"  Название товара (выбор человека): {name[:80]}")
    with open(back_dir / "product_name.txt", "w", encoding="utf-8", errors="replace") as f:
        f.write(name)


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


def pick_description_manually(page):
    """Человек кликает в открытом браузере на блок с описанием товара.

    Клик только подсвечивает кандидата; описание фиксируется отдельной
    кнопкой "Подтвердить" в оверлее, чтобы случайный клик не сработал как выбор.
    До включения режима выбора обычные клики по странице (вкладки, "Показать
    полностью") проходят как есть — человек может сначала раскрыть текст.
    Если описания на странице нет, "Подтвердить" можно нажать без выбора —
    тогда description.txt останется пустым и скриншоты раздела не снимаются.
    """
    result = pick_element_manually(
        page,
        'Раскройте описание на странице (вкладки, «Показать полностью»), включите выбор '
        'и кликните на блок с описанием. Если описания нет — нажмите «Подтвердить», ничего не выбирая.'
    )

    text = result["text"] if result else ""
    if result:
        print(f"  Описание (выбор человека): {text[:80]}...")
    else:
        print("  Описание не выбрано — блока с описанием на странице нет.")

    with open(back_dir / "description.txt", "w", encoding="utf-8", errors="replace") as f:
        f.write(text)

    if result:
        screenshot_description_and_specs(
            page, str(back_dir / "description_section.png"),
            scroll_y=result.get("scrollY"), end_y=result.get("endScrollY")
        )
    else:
        print("  Скриншоты раздела описания пропущены (описание не выбрано).")


# --- ЗАПУСК ---
if __name__ == "__main__":
    target_url = (sys.argv)[1]
    #
    with sync_playwright() as p:
        browser, page = open_site(p, target_url)

        # Выполняем первую часть задач — ручной выбор названия товара человеком
        time.sleep(3)
        pick_product_name_manually(page)
        make_main_screenshot(page)

        # Выполняем вторую часть — ручной выбор блока описания человеком
        pick_description_manually(page)
    """
        current_dir = os.path.dirname(os.path.abspath(__file__))
        compiler_path = os.path.join(current_dir, 'compiler.py')
        comp_res = subprocess.run(['python', compiler_path], capture_output=True, text=True)
        print(comp_res.stdout)
    """