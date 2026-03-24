from playwright.sync_api import sync_playwright
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
from datetime import datetime
import subprocess
import time
import os
import sys


# 1. Открытие сайта (через браузер пользователя)
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
        f"--user-data-dir={user_data_dir}",
        "--start-maximized"  # развёрнутое окно
        "--force-device-scale-factor=1"
        # или "--start-fullscreen"  # настоящий полноэкранный режим (F11)
    ])

    time.sleep(3)

    p = sync_playwright().start()
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
    context = browser.contexts[0]
    page = context.new_page()
    page.set_viewport_size({"width": 1920, "height": 1080})  #
    page.goto(url)

    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(10000)

    return p, browser, page


# 2. Скриншот страницы (без сохранения сразу)
def make_screenshot(page):
    screenshot = page.screenshot(full_page=False)
    return screenshot


# 3. Добавление времени на скрин
def add_timestamp(screenshot_bytes):
    image = Image.open(BytesIO(screenshot_bytes))
    draw = ImageDraw.Draw(image)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # позиция текста
    draw.text((10, image.height - 30), timestamp, fill="red")

    image.save("final_screenshot.png")
    return "final_screenshot.png"


# 4. Сохранение финального изображения (можно расширять)
def save_image(image_path):
    print(f"Сохранено: {image_path}")


# 5. Скрин ТОЛЬКО блока характеристик
def open_product_info_and_screenshot(page):
    from PIL import Image
    from io import BytesIO

    # 1. нажимаем "О товаре"
    button = page.get_by_text("Характеристики и описание")
    button.wait_for(timeout=10000)
    button.click()

    # 2. ждём загрузку
    page.wait_for_timeout(4000)

    # 3. делаем скрин ВСЕЙ страницы
    screenshot_bytes = page.screenshot(full_page=True)

    # 4. сохраняем
    image = Image.open(BytesIO(screenshot_bytes))
    image.save("second_screenshot.png")

    return "second_screenshot.png"


# --- MAIN ---
if __name__ == "__main__":

    url = sys.argv[1] #ссылка из веб, см ворд мэйн
    p, browser, page = open_site(url)

    # 1 скрин
    screenshot = make_screenshot(page)

    # добавляем время
    final_image = add_timestamp(screenshot)
    save_image(final_image)

    # 2 скрин (характеристики)
    second_image = open_product_info_and_screenshot(page)
    print(f"Сохранено: {second_image}")

    browser.close()
    p.stop()