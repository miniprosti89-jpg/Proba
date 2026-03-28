from playwright.sync_api import sync_playwright
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
from datetime import datetime
import subprocess
import time
import re


# 1. Открытие сайта
def open_site(url):
    p = sync_playwright().start()

    subprocess.Popen([
        "chromium",
        "--remote-debugging-port=9222",
        "--user-data-dir=/tmp/playwright",
        "--start-maximized",
        "--force-device-scale-factor=1"
    ])

    time.sleep(3)

    browser = p.chromium.connect_over_cdp("http://localhost:9222")
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    page = context.new_page()

    page.set_viewport_size({"width": 1920, "height": 1080})

    # Загружаем структуру страницы
    page.goto(url, wait_until="domcontentloaded")

    return p, browser, page


# 2. Сохранение названия и URL в TXT файл
def save_info_to_txt(page, url):
    selector = 'h2.productTitle--lfc4o'
    try:
        # Ждем появления названия товара
        element = page.wait_for_selector(selector, timeout=10000)
        product_name = element.inner_text().strip()

        # Сохраняем: первая строка - имя, вторая - ссылка
        with open("product_name.txt", "w", encoding="utf-8") as f:
            f.write(f"{product_name}\n")
            f.write(f"{url}\n")

        print(f"Данные (имя + url) сохранены в product_name.txt")
    except Exception as e:
        print(f"Не удалось извлечь название: {e}")
        # Если название не нашли, сохраним хотя бы ссылку
        with open("product_name.txt", "w", encoding="utf-8") as f:
            f.write(f"Название не найдено\n{url}\n")


# 3. Создание скриншота
def make_screenshot(page):
    return page.screenshot(full_page=False)


# 4. Добавление ТОЛЬКО времени на изображение
def add_timestamp_only(screenshot_bytes):
    image = Image.open(BytesIO(screenshot_bytes))
    draw = ImageDraw.Draw(image)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        # Пытаемся загрузить шрифт, иначе используем стандартный
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 40)
    except:
        font = ImageFont.load_default()

    # Рисуем только время (красным цветом)
    draw.text((20, image.height - 60), timestamp, fill="red", font=font)

    save_path = "final_screenshot.png"
    image.save(save_path)
    return save_path


# --- ОСНОВНОЙ ЦИКЛ ---
if __name__ == "__main__":
    url = input("Вставь ссылку: ")

    p, browser, page = open_site(url)

    # Шаг 1: Сохраняем инфо в текстовый файл
    save_info_to_txt(page, url)

    # Шаг 2: Делаем скрин
    screenshot_raw = make_screenshot(page)

    # Шаг 3: Наносим время на PNG
    final_path = add_timestamp_only(screenshot_raw)

    print(f"Скриншот сохранен: {final_path}")

    input("\nНажми Enter для выхода...")
    browser.close()
    p.stop()