from playwright.sync_api import sync_playwright
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime
import subprocess
import time
import os
from io import BytesIO

# 1. Открывает сайт
def open_site(url):
    chromium_path = None

    if os.name == "posix":  # Linux / Mac
        chromium_path = "chromium"
    elif os.name == "nt":  # Windows
        # Пробуем стандартные пути установки Chrome
        possible_paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ]
        for path in possible_paths:
            if os.path.exists(path):
                chromium_path = path
                break
        if chromium_path is None:
            raise FileNotFoundError("Chrome не найден. Укажи путь вручную в possible_paths.")

    user_data_dir = "/tmp/chrome-debug" if os.name == "posix" else r"C:\Temp\chrome-debug"

    subprocess.Popen([
        chromium_path,
        "--remote-debugging-port=9222",
        f"--user-data-dir={user_data_dir}"
    ])

    time.sleep(3)

    p = sync_playwright().start()
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
    context = browser.contexts[0]
    page = context.new_page()
    page.goto(url)

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
    desc_tab = page.get_by_text("Описание").first
    desc_tab.click()
    page.wait_for_timeout(1000)

    desc_block = page.get_by_text("Основные").locator("xpath=ancestor::div[3]").first
    desc_block.scroll_into_view_if_needed()
    page.wait_for_timeout(500)
    desc_screenshot_bytes = desc_block.screenshot()
    Image.open(BytesIO(char_screenshot_bytes)).save("description_screenshot.png")


    # Скриншот блока
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