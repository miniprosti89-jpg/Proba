import streamlit as st
import subprocess
import sys
import os

st.set_page_config(page_title="Веб-интерфейс на Python", layout="centered")

# ── Инициализация списка URL-ов в session_state ──────────────────────────────
if "url_entries" not in st.session_state:
    st.session_state.url_entries = [{"url": "", "criteria": []}]

# ── Вспомогательная функция: запустить один скрипт и показать результат ───────
def run_script(label, command):
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding='cp1251',
            errors='replace'
        )
        if result.returncode == 0:
            st.success(f"✅ {label} — успех!")
            st.text_area(f"Лог ({label}):", result.stdout, key=f"log_{label}_{id(command)}")
        else:
            st.error(f"❌ {label} — ошибка")
            st.code(result.stderr)
    except Exception as e:
        st.error(f"Не удалось запустить {label}: {e}")

# ─────────────────────────────────────────────────────────────────────────────
with st.container():
    st.title("Локальный интерфейс")


    # ── Поля ввода для каждого URL ────────────────────────────────────────────
    entries = st.session_state.url_entries
    count = len(entries)
    st.write(f"**Количество URL: {count}**")


    st.divider()
    if st.button("🔴 Закрыть приложение"):
        import streamlit.components.v1 as components
        components.html("""
            <script>window.top.close();</script>
        """, height=0)

        import threading

        def shutdown():
            import time
            time.sleep(1)
            os._exit(0)

        threading.Thread(target=shutdown, daemon=True).start()


    for i, entry in enumerate(entries):
        st.markdown(f"---\n#### URL №{i + 1}")
        entry["url"] = st.text_input(
            label=f"Ссылка {i + 1}:",
            value=entry["url"],
            placeholder="https://example.com",
            key=f"url_{i}"
        )
        entry["criteria"] = st.multiselect(
            label=f"Критерии для URL {i + 1} (1–4):",
            options=[1, 2, 3, 4],
            default=entry["criteria"],
            placeholder="Нажмите чтобы увидеть выпадающий список",
            key=f"criteria_{i}"
        )

    st.markdown("---")

    # ── Кнопка «Добавить URL» ─────────────────────────────────────────────────
    if st.button("➕ Добавить URL"):
        st.session_state.url_entries.append({"url": "", "criteria": []})
        st.rerun()

    # ── Кнопка «Удалить последний» (если больше одного) ──────────────────────
    if count > 1 and st.button("➖ Удалить последний URL"):
        st.session_state.url_entries.pop()
        st.rerun()

    # ── Кнопка «Создать отчёт» ────────────────────────────────────────────────
    if st.button("Создать отчёт"):
        current_dir = os.path.dirname(os.path.abspath(__file__))
        parent_dir  = os.path.dirname(current_dir)
        parcer_path   = os.path.join(parent_dir, "Back", "parcer.py")
        compiler_path = os.path.join(parent_dir, "Back", "compiler.py")
        word_path     = os.path.join(current_dir, "word_redactor.py")

        has_error = False
        for entry in st.session_state.url_entries:
            if not entry["url"]:
                st.error("⚠️ Одна из ссылок пустая!")
                has_error = True
            if not entry["criteria"]:
                st.error("⚠️ Для одной из ссылок не выбраны критерии!")
                has_error = True

        if not has_error:
            for i, entry in enumerate(st.session_state.url_entries):
                url_input    = entry["url"]
                criteria_str = ",".join(map(str, sorted(entry["criteria"])))

                st.markdown(f"### Обработка URL №{i + 1}: `{url_input}`")

                run_script(
                    f"Парсинг (URL {i + 1})",
                    [sys.executable, parcer_path, url_input, criteria_str]
                )
                run_script(
                    f"Создание JSON (URL {i + 1})",
                    [sys.executable, compiler_path, url_input, criteria_str]
                )
                run_script(
                    f"Создание Word (URL {i + 1})",
                    [sys.executable, word_path, url_input, criteria_str]
                )

