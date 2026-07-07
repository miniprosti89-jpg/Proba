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
    # Скрипт может подолгу ждать ручного выбора в браузере (right-click/Esc) —
    # subprocess.run(capture_output=True) отдаёт stdout только после того, как
    # процесс уже завершился, поэтому пока он висит, в интерфейсе не видно
    # вообще ничего. Здесь лог обновляется live по мере поступления строк —
    # это и полезнее для пользователя, и позволяет видеть, на чём именно
    # скрипт застрял, если он не завершается.
    placeholder = st.empty()
    lines = []
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='cp1251',
            errors='replace',
            bufsize=1,
        )
        for line in iter(process.stdout.readline, ''):
            lines.append(line)
            placeholder.code(f"{label}:\n" + "".join(lines))
        process.stdout.close()
        returncode = process.wait()

        log_text = "".join(lines)
        if returncode == 0:
            placeholder.empty()
            st.success(f"✅ {label} — успех!")
            st.text_area(f"Лог ({label}):", log_text, key=f"log_{label}_{id(command)}")
        else:
            placeholder.empty()
            st.error(f"❌ {label} — ошибка")
            st.code(log_text)
    except Exception as e:
        st.error(f"Не удалось запустить {label}: {e}")

# ─────────────────────────────────────────────────────────────────────────────
with st.container():
    # ── Поля ввода для каждой ссылки ──────────────────────────────────────────
    entries = st.session_state.url_entries

    for i, entry in enumerate(entries):
        entry["url"] = st.text_input(
            label=f"Вставьте ссылку:",
            value=entry["url"],
            placeholder="https://example.com",
            key=f"url_{i}"
        )
        st.write(f"Критерии (1–4):")
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
        st.divider()

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

                # -u — без буферизации: иначе print() в дочернем процессе
                # копится во внутреннем буфере Python и не доходит до нашего
                # live-лога, пока буфер не заполнится или процесс не завершится.
                run_script(
                    f"Парсинг (ссылка {i + 1})",
                    [sys.executable, "-u", parcer_path, url_input, criteria_str]
                )
                run_script(
                    f"Создание JSON (ссылка {i + 1})",
                    [sys.executable, "-u", compiler_path, url_input, criteria_str]
                )
                run_script(
                    f"Создание Word (ссылка {i + 1})",
                    [sys.executable, "-u", word_path, url_input, criteria_str]
                )

