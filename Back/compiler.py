import json
import os


def build_json():
    # 1. Читаем название товара и URL из одного файла
    try:
        with open('product_name.txt', 'r', encoding='utf-8') as f:
            lines = f.readlines()
            # Берем 1-ю строку (индекс 0) как название
            product_name = lines[0].strip() if len(lines) > 0 else ""
            # Берем 2-ю строку (индекс 1) как URL
            url_value = lines[1].strip() if len(lines) > 1 else ""
    except FileNotFoundError:
        print("Ошибка: файл product_name.txt не найден")
        return

    # 2. Читаем описание целиком
    try:
        with open('description.txt', 'r', encoding='utf-8') as f:
            description = f.read().strip()
    except FileNotFoundError:
        description = ""

    # 3. Собираем структуру
    result_data = {
        "url": url_value,
        "tovar": product_name,
        "desc": description,
        "screens": {
            "s1": "final_screenshot.png",
            "s2": "only_modal.png"
        }
    }

    # 4. Сохраняем результат
    with open('result.json', 'w', encoding='utf-8') as f:
        json.dump(result_data, f, ensure_ascii=False, indent=4)

    print("Готово! Проверьте файл result.json")


if __name__ == "__main__":
    build_json()