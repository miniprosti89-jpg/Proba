#!/usr/bin/env python3
"""
Генератор решений Роспотребнадзора о БАД — версия на основе шаблона.

Использование:
    python generate_from_template.py [путь/к/report.json]

Требует:
    - template.docx   (шаблон с метками {{ ... }})
    - report.json     (данные для подстановки)
    - pip install docxtpl
"""
#from web import choices
import json
import re
import sys
import os
from docxtpl import DocxTemplate, InlineImage
from docx.shared import Mm
from urllib.parse import urlparse

# ═══════════════════════════════════════════════════════════════════════════════
#  ТЕКСТЫ РЕШЕНИЙ ПО КРИТЕРИЯМ
# ═══════════════════════════════════════════════════════════════════════════════

# Текстовые фрагменты для каждого критерия (без URL-префикса и без сноски)
CRITERIA_TEXTS = {
    1: ("содержит запрещенную к распространению в информационно-телекоммуникационной "
        "сети «Интернет» информацию, содержащую предложение о розничной торговле "
        "биологически активными добавками, в том числе дистанционным способом, "
        "не прошедшей государственную регистрацию"),
    2: ("содержит запрещенную к распространению в информационно-телекоммуникационной "
        "сети «Интернет» информацию, содержащую предложение о розничной торговле "
        "биологически активными добавками, в том числе дистанционным способом, "
        "являющимися опасными в соответствии с законодательством Российской Федерации"),
    3: ("включающей сведения о наличии в составе таких биологически активных добавок "
        "нормируемых веществ в количествах, не соответствующих установленным в "
        "соответствии с законодательством Российской Федерации значениям"),
    4: "под видом пищевой добавки",
}


def build_decision_text(criteria_nums: list, url: str) -> str:
    """
    Склеивает тексты критериев в один абзац (по возрастанию номеров).
    Например, [2, 3] →
      «Ссылка {url} содержит... являющимися опасными... включающей сведения...
       (п. 2, 3 Критериев*).»
    """
    nums_sorted = sorted(set(criteria_nums))

    fragments = [CRITERIA_TEXTS[n] for n in nums_sorted]
    body = ", а также ".join(fragments)

    nums_str = ", ".join(str(n) for n in nums_sorted)
    suffix = f" (п. {nums_str} Критериев*)."

    return f"Ссылка {url} {body}{suffix}"

# ═══════════════════════════════════════════════════════════════════════════════
#  ЗАПОЛНЕНИЕ ШАБЛОНА
# ═══════════════════════════════════════════════════════════════════════════════

def fill_template(template_path: str, report: dict,
                  criteria_nums: list, output_path: str):
    doc = DocxTemplate(template_path)

    url = report["url"]

    # парсинг названия сайта
    parsed = urlparse(url)
    site = parsed.netloc  # www.ozon.ru
    site = site.replace("www.", "")  # ozon.ru
    print(site)

    # Формируем список абзацев решения (по одному на каждый критерий)
    decisions = build_decision_text(criteria_nums, url)
    context = {
        "time": report.get("time", ""),
        "url":   report.get("url", ""),
        "site": site,
        "tovar": report.get("tovar", ""),
        "desc": report.get("desc", ""),
        "resh": decisions,
        "screenshots": [] # Список для хранения объектов изображений
    }

    # --- ЛОГИКА ВСТАВКИ СКРИНШОТА ---
    screens_dict = report.get("screens", {})  # Получаем словарь из JSON

    if isinstance(screens_dict, dict):
        _back_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Back")
        for key in sorted(screens_dict.keys()):
            img_path = screens_dict[key]
            # Если путь относительный — ищем файл в back_dir
            if not os.path.isabs(img_path):
                img_path = os.path.join(_back_dir, img_path)
            if not os.path.exists(img_path):
                print(f"[ПРЕДУПРЕЖДЕНИЕ] Скриншот не найден: {img_path}")
                continue
            context["screenshots"].append(InlineImage(doc, img_path, width=Mm(259), height=Mm(144)))

    doc.render(context)

    # После рендера вставляем разрыв страницы после каждого параграфа с изображением,
    # чтобы каждый скриншот занимал свою страницу целиком
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    for para in list(doc.docx.paragraphs):
        if not para._element.findall('.//' + qn('w:drawing')):
            continue
        pb_para = OxmlElement('w:p')
        pb_run  = OxmlElement('w:r')
        pb_br   = OxmlElement('w:br')
        pb_br.set(qn('w:type'), 'page')
        pb_run.append(pb_br)
        pb_para.append(pb_run)
        para._element.addnext(pb_para)

    doc.save(output_path)


# ═══════════════════════════════════════════════════════════════════════════════
#  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ═══════════════════════════════════════════════════════════════════════════════

def cleanup_screenshots(back_dir: str):
    """Удаляет все файлы description_section_*.png из папки Back."""
    import glob
    pattern = os.path.join(back_dir, "description_section_*.png")
    files = glob.glob(pattern)
    for f in files:
        try:
            os.remove(f)
            print(f"Удалён: {os.path.basename(f)}")
        except Exception as e:
            print(f"[ПРЕДУПРЕЖДЕНИЕ] Не удалось удалить {f}: {e}")
    if files:
        print(f"Очистка: удалено {len(files)} файл(ов) description_section.")


def load_report(path: str = "report.json") -> dict:
    if not os.path.exists(path):
        print(f"[ОШИБКА] Файл '{path}' не найден.")
        sys.exit(1)

    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ═══════════════════════════════════════════════════════════════════════════════
#  ЗАПУСК
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    # Проверяем аргументы командной строки
    # sys.argv[1] - это ссылка (url_input)
    # sys.argv[2] - это критерии строкой "1,2"
    if len(sys.argv) < 3:
        print("Ошибка: недостаточно аргументов. Нужно передать URL и критерии.")
        return

    url_from_web = sys.argv[1]
    try:
        # Превращаем строку "1,2" обратно в список чисел [1, 2]
        criteria_nums = [int(x) for x in sys.argv[2].split(',')]
    except ValueError:
        print("Ошибка: критерии должны быть числами, разделенными запятой.")
        return

    # НАСТРОЙКА ПУТЕЙ
    current_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(current_dir)
    back_dir = os.path.join(parent_dir, "Back")
    report_path = os.path.join(back_dir, "report.json")
    template_path = os.path.join(current_dir, "template.docx")

    if not os.path.exists(template_path):
        print(f"[ОШИБКА] Шаблон '{template_path}' не найден.")
        return

    # ЗАГРУЗКА JSON
    report = load_report(report_path)

    # замена url из json введённым
    report['url'] = url_from_web


    print(f"Введенный URL: {url_from_web}")
    print(f"Выбранные критерии: {criteria_nums}")

    tovar = report.get("tovar", "").strip()
    # Обрезаем до 60 символов и убираем символы, запрещённые в именах файлов
    tovar_safe = re.sub(r'[\\/*?:"<>|]', '', tovar)[:30].strip()
    output_name = f"Результат_{criteria_nums}_{tovar_safe}.docx" if tovar_safe else f"Результат_{criteria_nums}.docx"
    output_path = os.path.join(parent_dir, output_name)

    # Запускаем генерацию
    fill_template(template_path, report, criteria_nums, output_path)

    print(f"\nГотово! Сохранено в: {output_path}")
    print("=" * 60)

    cleanup_screenshots(back_dir)


if __name__ == "__main__":
    main()