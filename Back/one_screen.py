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
        "--start-maximized"  # развёрнутое окно
        "--force-device-scale-factor=1"
    ])

    time.sleep(3)  # ждём запуск браузера

    browser = p.chromium.connect_over_cdp("http://localhost:9222")
    context = browser.contexts[0]
    page = context.new_page()

    page.set_viewport_size({"width": 1920, "height": 1080})  #
    page.goto(url)
    page.wait_for_timeout(5000)  # ждём загрузку

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

    # 👉 указываем размер шрифта
    font = ImageFont.truetype("DejaVuSans-Bold.ttf", 40)  # размер 40

    # позиция текста
    draw.text((10, image.height - 50), timestamp, fill="red", font=font)

    image.save("final_screenshot.png")
    return "final_screenshot.png"


# 4. Сохранение финального изображения (можно расширять)
def save_image(image_path):
    print(f"Сохранено: {image_path}")


# --- MAIN ---
if __name__ == "__main__":
    url = input("Вставь ссылку: ")

    p, browser, page = open_site(url)

    # 1 скрин
    screenshot = make_screenshot(page)

    # добавляем время
    final_image = add_timestamp(screenshot)
    save_image(final_image)


    input("Нажми Enter для выхода...")
    browser.close()
    p.stop()