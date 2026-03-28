import subprocess
import time
from io import BytesIO
from datetime import datetime
from playwright.sync_api import sync_playwright
from PIL import Image, ImageDraw, ImageFont

# --- НАСТРОЙКИ И ПУТИ ---
DEBUG_PORT = "9222"
USER_DATA_DIR = "/tmp/playwright"
CHROME_PATH = "chromium"  # Убедитесь, что chromium доступен в системе


def open_site(p, url):
    """Запуск браузера и переход на страницу."""
    subprocess.Popen([
        CHROME_PATH,
        f"--remote-debugging-port={DEBUG_PORT}",
        f"--user-data-dir={USER_DATA_DIR}",
        "--start-maximized",
        "--force-device-scale-factor=1"
    ])

    print("Ожидание запуска браузера...")
    time.sleep(5)

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
        f.write(f"{product_name}\n{url}\n")
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


def process_modal_info(page):
    """Кликает на характеристики, сохраняет текст и скриншот модалки."""
    try:
        # Ищем кнопку
        btn = page.locator("text='Характеристики и описание'").first
        btn.scroll_into_view_if_needed()
        page.wait_for_timeout(1000)
        btn.click()
        print("Кнопка характеристик нажата.")

        # Ждем модальное окно
        modal_selector = "div[role='dialog'], .popup-container, .modal-content, .shared-modal"
        modal = page.wait_for_selector(modal_selector, state="visible", timeout=15000)
        page.wait_for_timeout(1000)  # Даем анимации завершиться

        # 1. Извлекаем текст описания
        text_selector = "p.descriptionText--Jq9n2"
        description_element = modal.query_selector(text_selector)

        if description_element:
            desc_text = description_element.inner_text()
            with open("description.txt", "w", encoding="utf-8") as f:
                f.write(desc_text)
            print("Текст описания сохранен в description.txt")
        else:
            print("Текст описания внутри модального окна не найден.")

        # 2. Скриншот только модального окна
        modal.screenshot(path="only_modal.png")
        print("Скриншот модального окна сохранен в only_modal.png")

    except Exception as e:
        print(f"Ошибка при работе с модальным окном: {e}")


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