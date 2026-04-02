import json
from pathlib import Path
from datetime import datetime

back_dir = Path(__file__).parent

def build_json():
    timestamp = datetime.now().strftime("%d.%m.%Y (%H:%M)")
    # 1. Читаем название товара и URL из одного файла
    try:
        with open(back_dir / 'product_name.txt', 'r', encoding='utf-8') as f:
            lines = f.readlines()
            # Берем 1-ю строку (индекс 0) как название
            product_name = lines[0].strip() if len(lines) > 0 else ""
    except FileNotFoundError:
        print("Ошибка: файл product_name.txt не найден")
        return

    # 2. Читаем описание целиком
    try:
        with open(back_dir / 'description.txt', 'r', encoding='utf-8') as f:
            description = f.read().strip()
    except FileNotFoundError:
        description = ""

    # 3. Собираем список скриншотов описания (description_section_1.png, _2.png, ...)
    screens = {"s1": "final_screenshot.png"}
    i = 0
    while True:
        name = f"description_section_{i}.png"
        if (back_dir / name).exists():
            screens[f"s{i + 1}"] = name
            i += 1
        else:
            break

    # 4. Собираем структуру
    result_data = {
        "time": timestamp,
        "url": "",
        "tovar": product_name,
        "desc": description,
        "screens": screens
    }

    # 4. Сохраняем результат
    with open(back_dir / 'report.json', 'w', encoding='utf-8') as f:
        json.dump(result_data, f, ensure_ascii=False, indent=4)
    print("Готово! Проверьте файл report.json")


if __name__ == "__main__":
    build_json()