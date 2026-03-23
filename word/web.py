import streamlit as st
import subprocess
import sys
import os

# Настройка конфигурации страницы (заголовок во вкладке браузера)
st.set_page_config(page_title="Веб-интерфейс на Python", layout="centered")

# Используем "контейнер", чтобы визуально сгруппировать элементы по центру
with st.container():
    st.title("Локальный интерфейс")

    # 1. Поле для вставки ссылки
    # label - описание над полем, placeholder - подсказка внутри
    url_input = st.text_input(
        label="Введите ссылку:",
        placeholder="https://example.com"
    )

    # 2. Поле для ввода цифр от 1 до 4
    # number_input автоматически ограничивает ввод и добавляет кнопки +/-
    choices = st.multiselect(
        label="Выберите номера критериев (1-4):",
        placeholder="Нажмите чтобы увидеть выпадающий список",
        options=[1, 2, 3, 4],
        default=[]
    )


# НАЖАТИЕ КНОПКИ
    if st.button("Создать отчёт"):
        if not url_input:
            st.error("⚠️ Введите ссылку!")
        elif not choices:
            st.warning("⚠️ Выберите хотя бы один критерий.")
        else:
            st.info("🚀 Запуск процесса...")

            # НАСТРОЙКА ПУТЕЙ
            current_dir = os.path.dirname(os.path.abspath(__file__))
            parent_dir = os.path.dirname(current_dir)
            # Форматируем критерии в строку "1,2,3"
            criteria_str = ",".join(map(str, sorted(choices)))


            #Запуск парсера
            parcer_path = os.path.join(parent_dir, "Back/parcer.py")
            command = [sys.executable, parcer_path, url_input, criteria_str]

            try:
                # 3. Запуск. Используем cp1251 для Windows, чтобы не было ошибок декодирования
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    encoding='cp1251',
                    errors='replace'
                )

                if result.returncode == 0:
                    st.success("✅ Готово!")
                    st.text_area("Лог выполнения:", result.stdout)
                else:
                    st.error("❌ Ошибка при выполнении скрипта")
                    st.code(result.stderr)

            except Exception as e:
                st.error(f"Не удалось запустить файл: {e}")



            #Запуск ворда
            script_path = os.path.join(current_dir, "word_redactor.py")
            command = [sys.executable, script_path, url_input, criteria_str]

            try:
                # 3. Запуск. Используем cp1251 для Windows, чтобы не было ошибок декодирования
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    encoding='cp1251',
                    errors='replace'
                )

                if result.returncode == 0:
                    st.success("✅ Готово!")
                    st.text_area("Лог выполнения:", result.stdout)
                else:
                    st.error("❌ Ошибка при выполнении скрипта")
                    st.code(result.stderr)

            except Exception as e:
                st.error(f"Не удалось запустить файл: {e}")