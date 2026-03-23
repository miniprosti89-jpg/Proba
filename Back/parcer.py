from playwright.sync_api import sync_playwright
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
from datetime import datetime
import subprocess
import time


# 1. Открытие сайта (через браузер пользователя)
def open_site(url):
    p = sync_playwright().start()

    # запускаем chromium с remote debugging
    subprocess.Popen([
        "chromium",
        "--remote-debugging-port=9222",
        "--user-data-dir=/tmp/playwright"
    ])

    time.sleep(3)  # ждём запуск браузера

    browser = p.chromium.connect_over_cdp("http://localhost:9222")
    context = browser.contexts[0]
    page = context.new_page()

    page.goto(url)
    page.wait_for_timeout(5000)  # ждём загрузку

    return p, browser, page


# 2. Скриншот страницы (без сохранения сразу)
def make_screenshot(page):
    screenshot = page.screenshot(full_page=True)
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
    button = page.get_by_text("О товаре")
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
    url = input("Вставь ссылку: ")

    p, browser, page = open_site(url)

    # 1 скрин
    screenshot = make_screenshot(page)

    # добавляем время
    final_image = add_timestamp(screenshot)
    save_image(final_image)

    # 2 скрин (характеристики)
    second_image = open_product_info_and_screenshot(page)
    print(f"Сохранено: {second_image}")

    input("Нажми Enter для выхода...")
    browser.close()
    p.stop()