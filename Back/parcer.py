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


def wait_for_confirmed(page):
    """Ждёт window.__pdConfirmed === true без page.wait_for_function().

    wait_for_function (при любом polling — 'raf' или числовом интервале)
    строит предикат из строки через eval() ВНУТРИ страницы, чтобы опрашивать
    его без лишних CDP-round-trip'ов. На сайтах со строгим CSP без
    'unsafe-eval' (например, github.com) это падает с EvalError сразу же —
    ещё до того, как человек успевает что-то нажать. page.evaluate() же
    выполняется через CDP Runtime.evaluate "снаружи" страницы и её CSP не
    касается — поэтому опрашиваем флаг вручную из Python.
    """
    while not page.evaluate("window.__pdConfirmed"):
        page.wait_for_timeout(100)

# Многие сайты подключают собственный скрипт защиты от копирования, который
# глушит ПКМ/некоторые клавиши через window.addEventListener(..., {capture:true})
# и stopImmediatePropagation(). Если наш обработчик регистрируется позже (а
# page.evaluate() всегда выполняется уже ПОСЛЕ загрузки и скриптов страницы),
# он в такой гонке проигрывает и просто не получает событие.
# add_init_script выполняется до вообще любого скрипта страницы (в момент
# создания document, ещё до парсинга <head>), поэтому наш обработчик здесь
# регистрируется первым и получает событие раньше любой защиты сайта —
# независимо от того, что сайт сделает с событием после нас.
PICK_CORE_INIT_JS = r"""
(() => {
    if (window.__pdCore) return;

    let active = false;
    let onPick = null;
    let onEscape = null;

    function isClickable(el) {
        return !!(el && el.closest && el.closest('button, a, input, select, textarea, [role="button"]'));
    }

    window.addEventListener('mousedown', (e) => {
        if (!active || e.button !== 2) return;
        if (isClickable(e.target)) return;
        if (onPick) onPick(e.target);
    }, { capture: true });

    window.addEventListener('contextmenu', (e) => {
        if (!active) return;
        e.preventDefault();
        e.stopPropagation();
    }, { capture: true });

    window.addEventListener('keydown', (e) => {
        if (!active || e.key !== 'Escape') return;
        e.preventDefault();
        if (onEscape) onEscape();
    }, { capture: true });

    window.__pdCore = {
        start(pickCb, escCb) {
            active = true;
            onPick = pickCb;
            onEscape = escCb;
        },
        stop() {
            active = false;
            onPick = null;
            onEscape = null;
        },
    };
})();
"""


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
    page.add_init_script(PICK_CORE_INIT_JS)

    print(f"Переход по ссылке: {url}")
    page.goto(url, wait_until="domcontentloaded")

    # Вкладки, созданные через CDP, не всегда становятся активными в окне
    # браузера сами по себе — без этого человек может кликать/нажимать Esc
    # на другой (видимой) вкладке, а наш оверлей висит на невидимой.
    page.bring_to_front()

    return browser, page

    print("Возврат в начало страницы...")
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(400)


PICK_OVERLAY_JS = r"""
(instruction) => {
    const MARK = 'data-pd-overlay';
    window.__pdConfirmed = false;
    window.__pdResult = null;
    window.__pdHoverEl = null;

    // Отдельный AbortController на каждый вызов — гарантирует, что обработчики
    // снимаются вместе с оверлеем и не остаются висеть на следующем шаге.
    const controller = new AbortController();
    window.__pdAbort = controller;
    const { signal } = controller;

    // pointer-events: none — панель чисто информационная (без кнопок),
    // поэтому не должна перехватывать ни ЛКМ, ни ПКМ: клики проходят
    // "сквозь" неё к реальному содержимому страницы под курсором.
    const panel = document.createElement('div');
    panel.setAttribute(MARK, '1');
    panel.style.cssText = `
        position: fixed; left: 0px; top: 0px; z-index: 2147483647;
        background: #222; color: #fff; padding: 10px 14px; border-radius: 8px;
        font: 14px/1.4 sans-serif; box-shadow: 0 2px 10px rgba(0,0,0,.4);
        max-width: 320px; pointer-events: none;
    `;
    panel.textContent = instruction;
    document.body.appendChild(panel);

    // Клик правой кнопкой по кнопке/ссылке сайта (например, по самой кнопке-
    // триггеру, открывающей панель описания) почти никогда не то, что человек
    // хочет выбрать как текст — такие клики не считаем выбором блока.
    function isClickable(el) {
        return !!(el && el.closest && el.closest('button, a, input, select, textarea, [role="button"]'));
    }

    function setOutline(el, color) {
        if (!el) return;
        el.style.outline = color ? `3px solid ${color}` : '';
        el.style.outlineOffset = color ? '-3px' : '';
    }

    let lastX = 0;
    let lastY = 0;

    // Короткое уведомление снизу-слева от курсора. Без атрибута MARK — иначе
    // PICK_CLEANUP_JS (вызывается сразу после подтверждения выбора) удалит
    // его мгновенно, не дав провисеть отведённую секунду. Помечено отдельным
    // data-pd-toast — по нему make_main_screenshot прячет плашку на время
    // снимка, чтобы она не попадала на скриншоты.
    function showToast(text) {
        const toast = document.createElement('div');
        toast.setAttribute('data-pd-toast', '1');
        toast.textContent = text;
        toast.style.cssText = `
            position: fixed; left: 0px; top: 0px; z-index: 2147483647;
            color: #ff3b30; background: rgba(0,0,0,.85); padding: 10px 16px;
            border-radius: 8px; font: bold 16px/1.4 sans-serif;
            box-shadow: 0 2px 10px rgba(0,0,0,.4); visibility: hidden;
        `;
        document.body.appendChild(toast);
        const gap = 12;
        const tw = toast.offsetWidth;
        const th = toast.offsetHeight;
        const left = Math.max(4, Math.min(lastX - tw - gap, window.innerWidth - tw - 4));
        const top = Math.max(4, Math.min(lastY + gap, window.innerHeight - th - 4));
        toast.style.left = `${left}px`;
        toast.style.top = `${top}px`;
        toast.style.visibility = 'visible';
        setTimeout(() => toast.remove(), 1000);
    }

    // ПКМ по нужному элементу сразу подтверждает выбор — без отдельной кнопки.
    // Esc подтверждает "ничего не выбрано" (например, если названия нет).
    // Сам перехват ПКМ/Esc делает window.__pdCore (см. PICK_CORE_INIT_JS) —
    // он регистрируется через add_init_script раньше любых скриптов страницы
    // (в т.ч. её собственной защиты от копирования/ПКМ), а здесь мы только
    // подписываемся на его колбэки на время текущего выбора.
    //
    // Важно: core НЕ останавливаем здесь. mousedown срабатывает раньше
    // 'contextmenu' от того же самого клика — если тут же вызвать
    // pdCore.stop(), 'active' станет false ещё до того, как придёт
    // 'contextmenu', и core перестанет подавлять системное меню браузера
    // (оно всё-таки появится и заберёт себе фокус клавиатуры, из-за чего
    // Esc потом закрывает это меню вместо работы в нашем скрипте). Core
    // останавливается только в PICK_CLEANUP_JS, когда Python уже забрал
    // результат. done-флаг защищает от повторной обработки, если до этого
    // момента придёт ещё одно событие.
    let done = false;
    function confirmPick(pickedEl) {
        if (done) return;
        done = true;
        console.log('[pd] confirmPick', pickedEl);
        if (pickedEl) {
            const rect = pickedEl.getBoundingClientRect();
            window.__pdResult = {
                text: (pickedEl.innerText || '').trim(),
                scrollY: Math.round(rect.top + window.scrollY),
                endScrollY: Math.round(rect.bottom + window.scrollY),
            };
            showToast('Название выбрано');
        } else {
            window.__pdResult = null;
        }
        window.__pdConfirmed = true;
    }

    // Панель привязана к курсору и держится снизу-справа от него.
    function positionPanel(x, y) {
        const gap = 16;
        const pw = panel.offsetWidth;
        const ph = panel.offsetHeight;
        let left = Math.max(4, Math.min(x + gap, window.innerWidth - pw - 4));
        let top = Math.max(4, Math.min(y + gap, window.innerHeight - ph - 4));
        panel.style.left = `${left}px`;
        panel.style.top = `${top}px`;
    }
    positionPanel(0, 0);
    window.addEventListener('mousemove', (e) => {
        lastX = e.clientX;
        lastY = e.clientY;
        positionPanel(e.clientX, e.clientY);
    }, { capture: true, signal });

    // Подсветка элемента под курсором работает постоянно (ЛКМ при этом
    // свободна для обычного взаимодействия с сайтом — мы её не перехватываем).
    window.addEventListener('mouseover', (e) => {
        if (isClickable(e.target)) return;
        window.__pdHoverEl = e.target;
        setOutline(e.target, 'orange');
    }, { capture: true, signal });

    window.addEventListener('mouseout', (e) => {
        if (window.__pdHoverEl === e.target) {
            setOutline(e.target, null);
            window.__pdHoverEl = null;
        }
    }, { capture: true, signal });

    window.__pdCore.start(
        (el) => { console.log('[pd] pdCore pick', el); confirmPick(el); },
        () => { console.log('[pd] pdCore escape'); confirmPick(null); },
    );
}
"""

PICK_CLEANUP_JS = r"""
() => {
    if (window.__pdCore) window.__pdCore.stop();
    if (window.__pdAbort) {
        window.__pdAbort.abort();
        window.__pdAbort = null;
    }
    // Снимаем подсветку с наведённого элемента — иначе рамка останется на
    // реальном элементе страницы и попадёт на скриншоты.
    if (window.__pdHoverEl) {
        window.__pdHoverEl.style.outline = '';
        window.__pdHoverEl.style.outlineOffset = '';
    }
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

    // pointer-events: none — панель чисто информационная (без кнопок),
    // поэтому не должна перехватывать ни ЛКМ, ни ПКМ: клики проходят
    // "сквозь" неё к реальному содержимому страницы под курсором.
    const panel = document.createElement('div');
    panel.setAttribute(MARK, '1');
    panel.style.cssText = `
        position: fixed; left: 0px; top: 0px; z-index: 2147483647;
        background: #222; color: #fff; padding: 10px 14px; border-radius: 8px;
        font: 14px/1.4 sans-serif; box-shadow: 0 2px 10px rgba(0,0,0,.4);
        max-width: 320px; pointer-events: none;
    `;
    panel.textContent = instruction;
    document.body.appendChild(panel);

    // ПКМ по кнопке/ссылке сайта (например, по самой кнопке-триггеру,
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

    let lastX = 0;
    let lastY = 0;

    // Короткое уведомление снизу-слева от курсора. Без атрибута MARK — иначе
    // PICK_CLEANUP_JS (вызывается сразу после подтверждения выбора) удалит
    // его мгновенно, не дав провисеть отведённую секунду. Помечено отдельным
    // data-pd-toast — по нему make_main_screenshot прячет плашку на время
    // снимка, чтобы она не попадала на скриншоты.
    function showToast(text) {
        const toast = document.createElement('div');
        toast.setAttribute('data-pd-toast', '1');
        toast.textContent = text;
        toast.style.cssText = `
            position: fixed; left: 0px; top: 0px; z-index: 2147483647;
            color: #ff3b30; background: rgba(0,0,0,.85); padding: 10px 16px;
            border-radius: 8px; font: bold 16px/1.4 sans-serif;
            box-shadow: 0 2px 10px rgba(0,0,0,.4); visibility: hidden;
        `;
        document.body.appendChild(toast);
        const gap = 12;
        const tw = toast.offsetWidth;
        const th = toast.offsetHeight;
        const left = Math.max(4, Math.min(lastX - tw - gap, window.innerWidth - tw - 4));
        const top = Math.max(4, Math.min(lastY + gap, window.innerHeight - th - 4));
        toast.style.left = `${left}px`;
        toast.style.top = `${top}px`;
        toast.style.visibility = 'visible';
        setTimeout(() => toast.remove(), 1000);
    }

    // ПКМ по блоку сразу подтверждает его и добавляет в описание — без
    // отдельной кнопки "Подтвердить". Сам перехват ПКМ/Esc делает
    // window.__pdCore (см. PICK_CORE_INIT_JS) — он регистрируется через
    // add_init_script раньше любых скриптов страницы (в т.ч. её собственной
    // защиты от копирования/ПКМ), здесь мы только подписываемся на колбэки.
    //
    // Важно: core НЕ останавливаем здесь. mousedown срабатывает раньше
    // 'contextmenu' от того же самого клика — если тут же вызвать
    // pdCore.stop(), 'active' станет false ещё до того, как придёт
    // 'contextmenu', и core перестанет подавлять системное меню браузера
    // (оно всё-таки появится и заберёт себе фокус клавиатуры, из-за чего
    // Esc потом закрывает это меню вместо работы в нашем скрипте). Core
    // останавливается только в PICK_CLEANUP_JS, когда Python уже забрал
    // результат. done-флаг защищает от повторной обработки, если до этого
    // момента придёт ещё одно событие.
    let done = false;
    function confirmPick(pickedEl) {
        if (done) return;
        done = true;
        console.log('[pd] confirmPick', pickedEl);
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
        showToast('Описание выбрано');
    }

    // Esc заменяет кнопку "Завершить" — работает всегда, даже без выбранного
    // блока (на странице может не быть описания вовсе, или блоки закончились).
    function finishPicking() {
        if (done) return;
        done = true;
        console.log('[pd] finishPicking (Esc)');
        window.__pdResult = null;
        window.__pdAction = 'finish';
        window.__pdConfirmed = true;
    }

    // Панель привязана к курсору и держится снизу-справа от него.
    function positionPanel(x, y) {
        const gap = 16;
        const pw = panel.offsetWidth;
        const ph = panel.offsetHeight;
        let left = Math.max(4, Math.min(x + gap, window.innerWidth - pw - 4));
        let top = Math.max(4, Math.min(y + gap, window.innerHeight - ph - 4));
        panel.style.left = `${left}px`;
        panel.style.top = `${top}px`;
    }
    positionPanel(0, 0);
    window.addEventListener('mousemove', (e) => {
        lastX = e.clientX;
        lastY = e.clientY;
        positionPanel(e.clientX, e.clientY);
    }, { capture: true, signal });

    // Подсветка элемента под курсором работает постоянно (ЛКМ при этом
    // свободна для обычного взаимодействия с сайтом — мы её не перехватываем).
    window.addEventListener('mouseover', (e) => {
        if (isClickable(e.target)) return;
        window.__pdHoverEl = e.target;
        setOutline(e.target, 'orange');
    }, { capture: true, signal });

    window.addEventListener('mouseout', (e) => {
        if (window.__pdHoverEl === e.target) {
            setOutline(e.target, null);
            window.__pdHoverEl = null;
        }
    }, { capture: true, signal });

    window.__pdCore.start(
        (el) => { console.log('[pd] pdCore pick', el); confirmPick(el); },
        () => { console.log('[pd] pdCore escape'); finishPicking(); },
    );
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
    wait_for_confirmed(page)
    action = page.evaluate("window.__pdAction")
    result = page.evaluate("window.__pdResult")
    page.evaluate(PICK_CLEANUP_JS)
    return action, result


def pick_element_manually(page, instruction):
    """Показывает оверлей в браузере и ждёт, пока человек кликнет правой кнопкой
    мыши на нужный блок (клик сразу подтверждает выбор) либо нажмёт Esc, если
    блока нет. ЛКМ при этом свободна для обычного взаимодействия с сайтом.

    Возвращает {text, scrollY, endScrollY} выбранного элемента, либо None.
    """
    print(f"\n--- Ожидание ручного выбора: {instruction} ---")
    page.evaluate(PICK_OVERLAY_JS, instruction)
    wait_for_confirmed(page)
    result = page.evaluate("window.__pdResult")
    page.evaluate(PICK_CLEANUP_JS)
    return result


def pick_product_name_manually(page):
    """Человек кликает на название товара в открытом браузере. Сохраняет в product_name.txt."""
    result = pick_element_manually(
        page,
        'Кликните правой кнопкой мыши на название товара. Если названия нет — нажмите Esc.'
    )
    name = result["text"] if result else "Название не найдено"
    print(f"  Название товара (выбор человека): {name[:80]}")
    with open(back_dir / "product_name.txt", "w", encoding="utf-8", errors="replace") as f:
        f.write(name)


def make_main_screenshot(page, path=None):
    """Делает скриншот и накладывает дату/время.
    path=None → сохраняет в back_dir/final_screenshot.png (поведение по умолчанию).
    """
    # Прячем плашку "... выбрано" (data-pd-toast) на время снимка — она нужна
    # только человеку в браузере, а не на итоговом скриншоте.
    page.evaluate("document.querySelectorAll('[data-pd-toast]').forEach(el => el.style.visibility = 'hidden')")
    screenshot_bytes = page.screenshot(full_page=False)
    page.evaluate("document.querySelectorAll('[data-pd-toast]').forEach(el => el.style.visibility = 'visible')")
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
    'Раскройте описание на странице (вкладки, «Показать полностью») и кликните '
    'правой кнопкой мыши на блок с описанием — блок добавится, и откроется выбор '
    'следующего. Если блоков нет — нажмите Esc (описание останется пустым).'
)

NEXT_BLOCK_INSTRUCTION = (
    'Если описание продолжается в другом блоке — кликните на него правой кнопкой '
    'мыши. Блоков больше нет — нажмите Esc.'
)


def pick_description_manually(page):
    """Человек кликает в открытом браузере на блоки с описанием товара.

    Описание нередко разбито на несколько отдельных JS-объектов на странице,
    поэтому выбор циклический: после каждого подтверждённого блока сразу
    снимается его серия скриншотов, а оверлей открывается заново — для
    следующего блока. Нажатие Esc останавливает цикл и записывает
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