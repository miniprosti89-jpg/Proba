import pytesseract
from PIL import Image
import re
import json


# 1. Чтение текста с изображения
def extract_text(image_path):
    image = Image.open(image_path)
    text = pytesseract.image_to_string(image, lang="rus")
    return text


# 2. Разделение текста на характеристики и описание
def parse_characteristics(text):
    lines = text.split("\n")
    characteristics = []

    for line in lines:
        line = line.strip()

        if not line:
            continue

        # пропускаем всё после "Описание"
        if "Описание" in line:
            break

        characteristics.append(line)

    return characteristics


def is_valid_sentence(line):
    # должно быть минимум 5 слов
    words = line.split()
    if len(words) < 5:
        return False

    # слишком много капса = мусор
    upper_ratio = sum(1 for c in line if c.isupper()) / max(len(line), 1)
    if upper_ratio > 0.5:
        return False

    # слишком мало гласных → не русский текст
    vowels = "аеёиоуыэюя"
    vowel_count = sum(1 for c in line.lower() if c in vowels)
    if vowel_count < 3:
        return False

    return True


def parse_description(text):
    lines = text.split("\n")
    description = []

    is_description = False

    for line in lines:
        line = line.strip()

        if not line:
            continue

        # старт
        if "Описание" in line:
            is_description = True
            continue

        if not is_description:
            continue

        # стоп-блоки (очень важно)
        if any(stop_word in line for stop_word in [
            "Вы недавно смотрели",
            "Похожие товары",
            "Стань участником",
            "Покупателям",
            "Продавцам",
            "Наши проекты"
        ]):
            break

        # ❌ базовые фильтры
        if re.search(r"\d+\s?Р", line):
            continue

        if any(sym in line for sym in ["@", "©", "%", "$"]):
            continue

        if "кошельком" in line.lower():
            continue

        # ❌ новый умный фильтр
        if not is_valid_sentence(line):
            continue

        description.append(line)

    return description
def is_garbage_text(line):
    # 1. подозрительные символы
    if any(sym in line for sym in ["[", "]", "|", "—", "_", "/"]):
        return True

    words = line.split()

    bad_words = 0

    for word in words:
        # слово с мусором
        if re.search(r"[^а-яА-ЯёЁa-zA-Z\-]", word):
            bad_words += 1
            continue

        # слишком странное слово (много согласных подряд)
        if re.search(r"[бвгджзклмнпрстфхцчшщ]{5,}", word.lower()):
            bad_words += 1

    # если половина слов мусор — строка мусор
    if words and bad_words / len(words) > 0.4:
        return True

    return False
# 3. Сохранение в markdown
def save_to_md(characteristics, description):
    with open("result.json", "w", encoding="utf-8") as f:
        f.write("# Характеристики\n\n")
        for item in characteristics:
            f.write(f"- {item}\n")

        f.write("\n# Описание\n\n")
        for item in description:
            f.write(f"{item}\n")

    print("Файл result.json сохранён")



# --- MAIN ---
if __name__ == "__main__":
    image_path = "second_screenshot.png"

    text = extract_text(image_path)

    characteristics = parse_characteristics(text)
    description = parse_description(text)

    save_to_md(characteristics, description)