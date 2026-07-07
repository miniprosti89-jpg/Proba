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
    # ── Поля ввода для каждой ссылки ──────────────────────────────────────────
    entries = st.session_state.url_entries
    count = len(entries)

    for i, entry in enumerate(entries):
        st.markdown(f"#### Ссылка №{i + 1}")
        entry["url"] = st.text_input(
            label=f"Ссылка {i + 1}:",
            value=entry["url"],
            placeholder="https://example.com",
            key=f"url_{i}"
        )
        st.write(f"Критерии для ссылки {i + 1} (1–4):")
        crit_cols = st.columns(4)
        for n in range(1, 5):
            active = n in entry["criteria"]
            if crit_cols[n - 1].button(
                f"✅ {n}" if active else str(n),
                key=f"criteria_{i}_{n}",
                type="primary" if active else "secondary",
                use_container_width=True,
            ):
                if active:
                    entry["criteria"].remove(n)
                else:
                    entry["criteria"].append(n)
                st.rerun()
        if st.button("🗑️ Удалить эту ссылку", key=f"delete_{i}"):
            st.session_state.url_entries.pop(i)
            st.rerun()
        st.divider()

    # ── Кнопка «Добавить ссылку» ───────────────────────────────────────────────
    if st.button("➕ Добавить ссылку"):
        st.session_state.url_entries.append({"url": "", "criteria": []})
        st.rerun()

    # ── Кнопка «Удалить последнюю» (если больше одной) ────────────────────────
    if count > 1 and st.button("➖ Удалить последнюю ссылку"):
        st.session_state.url_entries.pop()
        st.rerun()

    # ── Кнопка «Создать отчёт» ────────────────────────────────────────────────
    st.markdown("""
        <style>
        .st-key-create_report button {
            background-color: #28a745;
            color: white;
            border-color: #28a745;
        }
        .st-key-create_report button:hover {
            background-color: #218838;
            color: white;
            border-color: #218838;
        }
        </style>
    """, unsafe_allow_html=True)
    if st.button("Создать отчёт", key="create_report"):
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

                st.markdown(f"### Обработка ссылки №{i + 1}: `{url_input}`")

                run_script(
                    f"Парсинг (ссылка {i + 1})",
                    [sys.executable, parcer_path, url_input, criteria_str]
                )
                run_script(
                    f"Создание JSON (ссылка {i + 1})",
                    [sys.executable, compiler_path, url_input, criteria_str]
                )
                run_script(
                    f"Создание Word (ссылка {i + 1})",
                    [sys.executable, word_path, url_input, criteria_str]
                )

