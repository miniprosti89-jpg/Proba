from playwright.sync_api import sync_playwright
from PIL import Image
from io import BytesIO
import subprocess
import time


def open_site(url):
    p = sync_playwright().start()

    # Запускаем браузер.
    # СОВЕТ: Если видишь "Проверяем браузер", не закрывай окно вручную,
    # дай ему пройти проверку (иногда нужно кликнуть "Я человек").
    subprocess.Popen([
        "chromium",
        "--remote-debugging-port=9222",
        "--user-data-dir=/tmp/playwright",
        "--start-maximized"
    ])
    time.sleep(5)

    browser = p.chromium.connect_over_cdp("http://localhost:9222")
    context = browser.contexts[0]
    page = context.new_page()
    page.goto(url)

    return p, browser, page


def screenshot_only_modal(page):
    # 1. Ждем, пока исчезнет "Проверяем браузер" и появится сам сайт.
    # Замени "body" на какой-то конкретный селектор сайта (например, ".header" или ".product-page")
  # Ждем появления любой кнопки или картинки

    # 2. Ищем кнопку открытия характеристик
    # WB/Ozon часто меняют селекторы, поэтому ищем по тексту
    btn = page.locator("text='Характеристики и описание'").first

    if not btn.is_visible():
        # Попробуем проскроллить, если кнопки не видно
        btn.scroll_into_view_if_needed()
        page.wait_for_timeout(1000)

    btn.click()
    print("Кнопка нажата. Ищу всплывающее окно...")

    # 3. КЛЮЧЕВОЙ МОМЕНТ: Ищем контейнер всплывающего окна.
    # Обычно у него есть класс modal, popup, dialog или фиксированное позиционирование.
    # Ниже универсальный селектор для большинства современных модалок:
    modal_selector = "div[role='dialog'], .popup-container, .modal-content, .shared-modal"

    try:
        modal = page.wait_for_selector(modal_selector, state="visible", timeout=10000)

        # Небольшая пауза, чтобы окно "долетело" (анимация)
        page.wait_for_timeout(600)

        # 4. Делаем скриншот ТОЛЬКО этого элемента
        # Playwright сам вырежет его по координатам
        screenshot_bytes = modal.screenshot()

        with open("only_modal.png", "wb") as f:
            f.write(screenshot_bytes)

        print("Готово! Скриншот окна сохранен в only_modal.png")
    except Exception as e:
        print(f"Не удалось найти модальное окно: {e}")


# --- MAIN ---
if __name__ == "__main__":
    url = input("Вставь ссылку: ")
    p, browser, page = open_site(url)

    screenshot_only_modal(page)

    input("Нажми Enter для выхода...")
    browser.close()
    p.stop()