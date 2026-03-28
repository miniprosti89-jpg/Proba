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
    """
    Функция находит кнопку характеристик, кликает по ней,
    извлекает текст описания и делает скриншот модального окна.
    """
    try:
        # 1. Ищем кнопку открытия характеристик/описания
        # Используем текст, так как классы могут меняться
        btn = page.locator("text='Характеристики и описание'").first

        if not btn.is_visible():
            print("Кнопка не видна, прокручиваю страницу...")
            btn.scroll_into_view_if_needed()
            page.wait_for_timeout(1000)

        btn.click()
        print("Кнопка нажата. Ожидаю появления окна...")

        # 2. Ждем появления модального окна
        # Универсальный селектор для всплывающих окон
        modal_selector = "div[role='dialog'], .popup-container, .modal-content, .shared-modal"
        modal = page.wait_for_selector(modal_selector, state="visible", timeout=15000)

        # Небольшая пауза для завершения анимации открытия
        page.wait_for_timeout(800)

        # 3. ИЗВЛЕЧЕНИЕ ТЕКСТА
        # Используем часть вашего класса, которая выглядит наиболее уникальной
        text_selector = "p.descriptionText--Jq9n2"

        try:
            # Ждем именно этот параграф внутри модального окна
            description_element = modal.query_selector(text_selector)

            if description_element:
                description_text = description_element.inner_text()
                print("\n=== ТЕКСТ НАЙДЕН ===")
                print(description_text)
                print("====================\n")

                # Сохраняем текст в файл
                with open("description.txt", "w", encoding="utf-8") as text_file:
                    text_file.write(description_text)
            else:
                print("Предупреждение: Элемент с текстом не найден внутри модалки.")

        except Exception as text_err:
            print(f"Не удалось скопировать текст: {text_err}")

        # 4. СОЗДАНИЕ СКРИНШОТА
        # Playwright автоматически обрежет изображение по границам модального окна
        screenshot_bytes = modal.screenshot()
        with open("only_modal.png", "wb") as f:
            f.write(screenshot_bytes)

        print("Готово! Скриншот сохранен в 'only_modal.png', текст в 'description.txt'")

    except Exception as e:
        print(f"Критическая ошибка во время работы с окном: {e}")


# --- MAIN ---
if __name__ == "__main__":
    url = input("Вставь ссылку: ")
    p, browser, page = open_site(url)

    screenshot_only_modal(page)

    input("Нажми Enter для выхода...")
    browser.close()
    p.stop()