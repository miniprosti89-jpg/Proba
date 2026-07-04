import time
import re
import sys
from io import BytesIO
from datetime import datetime
from playwright.sync_api import sync_playwright
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

import browser_launch

back_dir = Path(__file__).parent

# Консоль Windows (cp1251) не умеет выводить символы вне своей кодировки.
# Переключаем stdout/stderr на UTF-8 с заменой неизвестных символов на '?'.
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='cp1251', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='cp1251', errors='replace')

# --- НАСТРОЙКИ И ПУТИ ---


def open_site(p, url):
    """Запуск браузера и переход на страницу (поддерживает Linux и Windows).

    Использует тот же отладочный порт/профиль, что и launcher.py (см.
    browser_launch.py) — благодаря этому страница товара открывается новой
    вкладкой в том же окне браузера, где уже открыт Streamlit-интерфейс,
    а не в отдельном, никак не связанном с ним окне.
    """
    if not browser_launch.ensure_debug_browser_running(wait_timeout=30):
        raise RuntimeError(
            "Не удалось запустить/подключиться к браузеру за 30с. "
            "Закройте все окна этого браузера и попробуйте снова."
        )

    browser = p.chromium.connect_over_cdp(f"http://localhost:{browser_launch.DEBUG_PORT}")

    context = browser.contexts[0] if browser.contexts else browser.new_context()
    page = context.new_page()

    print(f"Переход по ссылке: {url}")
    page.goto(url, wait_until="domcontentloaded")

    return browser, page

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

    // Клик по кнопке/ссылке сайта (например, по самой кнопке-триггеру,
    // открывающей панель описания) почти никогда не то, что человек хочет
    // выбрать как текст описания — такие клики не считаем выбором блока.
    function isClickable(el) {
        return !!(el && el.closest && el.closest('button, a, input, select, textarea, [role="button"]'));
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

    function confirmPick() {
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
    }

    // Некоторые сайты закрывают модалку/шторку с описанием по клику "снаружи"
    // себя (обработчик на document, часто на mousedown — до всплытия click).
    // Наша панель физически прикреплена к document.body, а не внутрь такой
    // шторки, поэтому клик по нашим кнопкам считается "внешним" и закрывает её.
    // window стоит в цепочке событий раньше document, поэтому перехватываем
    // здесь — mousedown/pointerdown просто гасим, а click по своим кнопкам
    // обрабатываем вручную, не давая событию вообще дойти до document.
    ['mousedown', 'pointerdown', 'mouseup', 'pointerup'].forEach((evt) => {
        window.addEventListener(evt, (e) => {
            if (isOverlay(e.target)) e.stopPropagation();
        }, { capture: true, signal });
    });

    window.addEventListener('mouseover', (e) => {
        if (!pickMode || isOverlay(e.target) || isClickable(e.target)) return;
        if (window.__pdHoverEl && window.__pdHoverEl !== window.__pdPickedEl) setOutline(window.__pdHoverEl, null);
        window.__pdHoverEl = e.target;
        if (window.__pdHoverEl !== window.__pdPickedEl) setOutline(window.__pdHoverEl, 'orange');
    }, { capture: true, signal });

    window.addEventListener('click', (e) => {
        if (isOverlay(e.target)) {
            e.preventDefault();
            e.stopPropagation();
            const btn = e.target.closest('button');
            if (btn === toggleBtn) setPickMode(!pickMode);
            else if (btn === confirmBtn) confirmPick();
            return;
        }
        if (!pickMode) return;
        e.preventDefault();
        e.stopPropagation();
        // Клик по кнопке/ссылке (например, по кнопке-триггеру, открывающей
        // панель) не считаем выбором блока — гасим клик, но не выбираем.
        if (isClickable(e.target)) return;
        if (window.__pdPickedEl && window.__pdPickedEl !== e.target) setOutline(window.__pdPickedEl, null);
        window.__pdPickedEl = e.target;
        setOutline(window.__pdPickedEl, 'red');
    }, { capture: true, signal });
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


PICK_LOOP_OVERLAY_JS = r"""
(instruction) => {
    const MARK = 'data-pd-overlay';
    window.__pdConfirmed = false;
    window.__pdAction = null;
    window.__pdResult = null;
    window.__pdPickedEl = null;
    window.__pdHoverEl = null;
    window.__pdScrollContainer = null;

    // Некоторые сайты показывают описание во всплывающей боковой панели/шторке
    // со своей внутренней прокруткой (сама страница при этом не скроллится).
    // Ищем ближайшего прокручиваемого предка выбранного элемента, чтобы потом
    // при скриншотах листать именно его, а не document/window.
    function findScrollableAncestor(el) {
        let node = el ? el.parentElement : null;
        while (node && node !== document.body && node !== document.documentElement) {
            const style = getComputedStyle(node);
            if ((style.overflowY === 'auto' || style.overflowY === 'scroll') &&
                node.scrollHeight > node.clientHeight + 20) {
                return node;
            }
            node = node.parentElement;
        }
        return null;
    }

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
        <button id="__pd_confirm" disabled style="margin-right:6px;padding:6px 10px;cursor:pointer;background:#1565c0;color:#fff;border:1px solid #0d47a1;border-radius:4px;">Подтвердить</button>
        <button id="__pd_finish" style="padding:6px 10px;cursor:pointer;background:#2e7d32;color:#fff;border:1px solid #1b5e20;border-radius:4px;">Завершить</button>
    `;
    document.body.appendChild(panel);

    const toggleBtn = panel.querySelector('#__pd_toggle');
    const confirmBtn = panel.querySelector('#__pd_confirm');
    const finishBtn = panel.querySelector('#__pd_finish');

    let pickMode = false;

    function isOverlay(el) {
        return !!(el && el.closest && el.closest(`[${MARK}]`));
    }

    // Клик по кнопке/ссылке сайта (например, по самой кнопке-триггеру,
    // открывающей панель описания) почти никогда не то, что человек хочет
    // выбрать как текст описания — такие клики не считаем выбором блока.
    function isClickable(el) {
        return !!(el && el.closest && el.closest('button, a, input, select, textarea, [role="button"]'));
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

    // "Подтвердить" добавляет текущий выбранный блок и позволяет продолжить
    // выбор следующего (недоступно, пока ничего не выбрано).
    function confirmPick() {
        const pickedEl = window.__pdPickedEl;
        if (!pickedEl || confirmBtn.disabled) return;
        setPickMode(false);
        const container = findScrollableAncestor(pickedEl);
        window.__pdScrollContainer = container;
        const rect = pickedEl.getBoundingClientRect();
        const base = container ? container.scrollTop : window.scrollY;
        // Если блок лежит внутри прокручиваемой панели — доскраливаем до
        // самого конца ЭТОЙ панели (scrollHeight), а не только до нижней
        // границы кликнутого элемента. Иначе если рядом (ниже, вне экрана
        // в момент клика) есть соседний блок текста (например "Описание"
        // сразу после таблиц характеристик), он не попадёт в кадр, хотя
        // визуально клик выглядел как выбор "всей панели".
        const endScrollY = container
            ? container.scrollHeight
            : Math.round(rect.bottom + base);
        window.__pdResult = {
            text: (pickedEl.innerText || '').trim(),
            scrollY: Math.round(rect.top + base),
            endScrollY: endScrollY,
        };
        window.__pdAction = 'confirm';
        window.__pdConfirmed = true;
    }

    // "Завершить" работает всегда, даже без выбранного блока — на странице
    // может не быть описания вовсе, или все нужные блоки уже подтверждены.
    function finishPicking() {
        setPickMode(false);
        window.__pdResult = null;
        window.__pdAction = 'finish';
        window.__pdConfirmed = true;
    }

    // Некоторые сайты закрывают модалку/шторку с описанием по клику "снаружи"
    // себя (обработчик на document, часто на mousedown — до всплытия click).
    // Наша панель физически прикреплена к document.body, а не внутрь такой
    // шторки, поэтому клик по нашим кнопкам считается "внешним" и закрывает её.
    // window стоит в цепочке событий раньше document, поэтому перехватываем
    // здесь — mousedown/pointerdown просто гасим, а click по своим кнопкам
    // обрабатываем вручную, не давая событию вообще дойти до document.
    ['mousedown', 'pointerdown', 'mouseup', 'pointerup'].forEach((evt) => {
        window.addEventListener(evt, (e) => {
            if (isOverlay(e.target)) e.stopPropagation();
        }, { capture: true, signal });
    });

    window.addEventListener('mouseover', (e) => {
        if (!pickMode || isOverlay(e.target) || isClickable(e.target)) return;
        if (window.__pdHoverEl && window.__pdHoverEl !== window.__pdPickedEl) setOutline(window.__pdHoverEl, null);
        window.__pdHoverEl = e.target;
        if (window.__pdHoverEl !== window.__pdPickedEl) setOutline(window.__pdHoverEl, 'orange');
    }, { capture: true, signal });

    window.addEventListener('click', (e) => {
        if (isOverlay(e.target)) {
            e.preventDefault();
            e.stopPropagation();
            const btn = e.target.closest('button');
            if (btn === toggleBtn) setPickMode(!pickMode);
            else if (btn === confirmBtn) confirmPick();
            else if (btn === finishBtn) finishPicking();
            return;
        }
        if (!pickMode) return;
        e.preventDefault();
        e.stopPropagation();
        // Клик по кнопке/ссылке (например, по кнопке-триггеру, открывающей
        // панель) не считаем выбором блока — гасим клик, но не выбираем.
        if (isClickable(e.target)) return;
        if (window.__pdPickedEl && window.__pdPickedEl !== e.target) setOutline(window.__pdPickedEl, null);
        window.__pdPickedEl = e.target;
        setOutline(window.__pdPickedEl, 'red');
        confirmBtn.disabled = false;
    }, { capture: true, signal });
}
"""


def pick_description_block(page, instruction):
    """Один цикл выбора блока описания.

    Возвращает (action, result):
      action == "confirm" — result содержит {text, scrollY, endScrollY} блока;
      action == "finish"  — result всегда None, цикл выбора завершён.
    """
    print(f"\n--- Ожидание ручного выбора: {instruction} ---")
    page.evaluate(PICK_LOOP_OVERLAY_JS, instruction)
    page.wait_for_function("window.__pdConfirmed === true", timeout=0)
    action = page.evaluate("window.__pdAction")
    result = page.evaluate("window.__pdResult")
    page.evaluate(PICK_CLEANUP_JS)
    return action, result


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



# Скроллим window.__pdScrollContainer, если pick_description_block его нашёл
# (блок был внутри всплывающей панели/шторки со своей прокруткой) — иначе
# обычную страницу. Ссылка на контейнер сохраняется на window ещё в момент
# выбора блока (см. confirmPick в PICK_LOOP_OVERLAY_JS).
SCROLL_TO_JS = """(y) => {
    const c = window.__pdScrollContainer;
    if (c && document.contains(c)) { c.scrollTop = Math.max(0, y); }
    else { window.scrollTo(0, Math.max(0, y)); }
}"""

SCROLL_BY_JS = """(dy) => {
    const c = window.__pdScrollContainer;
    if (c && document.contains(c)) { c.scrollTop += dy; }
    else { window.scrollBy(0, dy); }
}"""

SCROLL_BOTTOM_JS = """() => {
    const c = window.__pdScrollContainer;
    if (c && document.contains(c)) { return c.scrollTop + c.clientHeight; }
    return window.scrollY + window.innerHeight;
}"""


def screenshot_description_and_specs(page, path="description_section.png", scroll_y=None, end_y=None, start_frame=1):
    """Серия скриншотов с датой/временем, покрывающая один блок описания.

    Мотаем к началу блока (scroll_y - 300), затем каждые 300px делаем скриншот
    через make_main_screenshot пока не достигнем end_y (нижний край блока).
    Если блок находится внутри всплывающей панели со своей прокруткой (не
    самой страницы), листаем именно её — см. SCROLL_TO_JS/SCROLL_BY_JS.
    Нумерация кадров начинается с start_frame — это позволяет вызывать функцию
    несколько раз подряд (для нескольких блоков описания) без перезаписи
    файлов предыдущих блоков.

    Возвращает следующий свободный номер кадра (для следующего вызова).
    """
    base, ext = path.rsplit(".", 1) if "." in path else (path, "png")

    # "- 300" вместо "- 200": даёт дополнительные ~100px запаса сверху,
    # чтобы перед первым скриншотом страница была чуть приподнята
    # и верх выбранного блока не оказывался у самого края кадра.
    page.evaluate(SCROLL_TO_JS, max(0, scroll_y - 300))
    page.wait_for_timeout(400)

    frame = start_frame
    while frame <= start_frame + 19:
        frame_path = f"{base}_{frame}.{ext}"
        make_main_screenshot(page, path=frame_path)

        # Достигли нижней границы блока?
        current_bottom = page.evaluate(SCROLL_BOTTOM_JS)
        if end_y is not None and current_bottom >= end_y:
            print(f"  Достигнут конец блока (end_y={end_y}), кадров: {frame - start_frame + 1}")
            break

        page.evaluate(SCROLL_BY_JS, 600)
        page.wait_for_timeout(300)
        frame += 1

    return frame + 1


FIRST_BLOCK_INSTRUCTION = (
    'Раскройте описание на странице (вкладки, «Показать полностью»), включите выбор '
    'и кликните на блок с описанием. «Подтвердить» — добавить блок и продолжить выбор '
    'следующего. «Завершить» — закончить (если блоков не было, описание останется пустым).'
)

NEXT_BLOCK_INSTRUCTION = (
    'Если описание продолжается в другом блоке — включите выбор, кликните на него '
    'и нажмите «Подтвердить». Блоков больше нет — нажмите «Завершить».'
)


def pick_description_manually(page):
    """Человек кликает в открытом браузере на блоки с описанием товара.

    Описание нередко разбито на несколько отдельных JS-объектов на странице,
    поэтому выбор циклический: после каждого подтверждённого блока сразу
    снимается его серия скриншотов, а оверлей открывается заново — для
    следующего блока. Нажатие "Завершить" останавливает цикл и записывает
    накопленный текст в description.txt (пусто, если блоков не было).
    """
    texts = []
    next_frame = 1

    while True:
        instruction = FIRST_BLOCK_INSTRUCTION if not texts else NEXT_BLOCK_INSTRUCTION
        action, result = pick_description_block(page, instruction)

        if action == "finish":
            break

        text = result["text"]
        texts.append(text)
        print(f"  Блок описания #{len(texts)} (выбор человека): {text[:80]}...")

        next_frame = screenshot_description_and_specs(
            page, str(back_dir / "description_section.png"),
            scroll_y=result.get("scrollY"), end_y=result.get("endScrollY"),
            start_frame=next_frame,
        )

    if texts:
        print(f"  Итоговое описание собрано из {len(texts)} блок(ов).")
    else:
        print("  Описание не выбрано — блоков с описанием на странице нет.")

    with open(back_dir / "description.txt", "w", encoding="utf-8", errors="replace") as f:
        f.write("\n\n".join(texts))


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