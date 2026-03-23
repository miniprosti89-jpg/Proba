from playwright.sync_api import sync_playwright
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime
import subprocess
import time
import os
from io import BytesIO

# 1. Открывает сайт
def open_site(url):
    # путь к chromium (кроссплатформенный минимум)
    chromium_path = None

    if os.name == "posix":  # Linux / Mac
        chromium_path = "chromium"
    elif os.name == "nt":  # Windows
        chromium_path = "chrome.exe"

    # запускаем браузер с remote debugging
    subprocess.Popen([
        chromium_path,
        "--remote-debugging-port=9222",
        "--user-data-dir=/tmp/chrome-debug"
    ])

    # ждём запуск браузера
    time.sleep(3)

    p = sync_playwright().start()

    # подключаемся
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")

    context = browser.contexts[0]

    # создаём новую вкладку
    page = context.new_page()
    page.goto(url)

    # ждём загрузку
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(10000)

    return p, browser, page


# 2. Делает скриншот
def take_screenshot(page):
    screenshot_bytes = page.screenshot(full_page=True)
    return screenshot_bytes


# 3. Добавляет время
def add_timestamp(image_bytes):
    image = Image.open(BytesIO(image_bytes))
    draw = ImageDraw.Draw(image)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    font = ImageFont.load_default()

    draw.text((10, image.height - 20), timestamp, fill="red", font=font)

    new_path = "final.png"
    image.save(new_path)

    return new_path

def open_product_info_and_screenshot(page):
    # -------------------------------
    # 1. Вкладка "О товаре"
    # -------------------------------
    tab_button = page.get_by_text("О товаре").first
    tab_button.wait_for(state="visible", timeout=15000)
    tab_button.click()
    page.wait_for_timeout(2000)

    # -------------------------------
    # 2. Скрин характеристик
    # -------------------------------
    char_tab = page.get_by_text("Характеристики").first
    char_tab.click()
    page.wait_for_timeout(1000)

    characteristics_block = page.get_by_text("Основные").locator("xpath=ancestor::div[3]").first
    characteristics_block.scroll_into_view_if_needed()
    page.wait_for_timeout(500)
    char_screenshot_bytes = characteristics_block.screenshot()
    Image.open(BytesIO(char_screenshot_bytes)).save("characteristics_tab.png")

    # -------------------------------
    # 3. Скрин всего блока "О товаре" с сохранением фона
    # -------------------------------
    description_block = page.get_by_text("Описание").locator("xpath=ancestor::div[3]").first
    description_block.scroll_into_view_if_needed()
    page.wait_for_timeout(500)

    # Принудительно устанавливаем фон блока перед скриншотом
    description_block.evaluate("""
    el => {
        const style = window.getComputedStyle(el);
        if (!style.backgroundColor || style.backgroundColor === 'rgba(0, 0, 0, 0)') {
            el.style.background = 'white';  // Можно указать любой цвет, например '#f5f5f5'
        }
    }
    """)

    # Скриншот блока
    desc_screenshot_bytes = description_block.screenshot()
    Image.open(BytesIO(desc_screenshot_bytes)).save("description_screenshot.png")
    return "characteristics_tab.png", "description_screenshot.png"

# 5. Сохраняет результат
def save_image(image_path):
    print(f"Сохранено: {image_path}")


# --- запуск ---
if __name__ == "__main__":
    url = input("Вставь ссылку: ")

    p, browser, page = open_site(url)
    screenshot = take_screenshot(page)
    final_image = add_timestamp(screenshot)
    second_image = open_product_info_and_screenshot(page)
    save_image(final_image)

    browser.close()
    p.stop()