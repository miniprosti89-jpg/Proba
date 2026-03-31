import json
from pathlib import Path

back_dir = Path(__file__).parent

def build_json():
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

    # 3. Собираем структуру
    result_data = {
        "url": "",
        "tovar": product_name,
        "desc": description,
        "screens": {
            "s1": "final_screenshot.png",
            "s2": "description_section_start.png",
            "s3": "description_section_end.png"
        }
    }

    # 4. Сохраняем результат
    with open(back_dir / 'report.json', 'w', encoding='utf-8') as f:
        json.dump(result_data, f, ensure_ascii=False, indent=4)
    print("Готово! Проверьте файл report.json")


if __name__ == "__main__":
    build_json()