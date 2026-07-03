import streamlit as st
import subprocess
import sys
import os

st.set_page_config(page_title="Веб-интерфейс на Python", layout="centered")

# ── Инициализация списка URL-ов в session_state ──────────────────────────────
if "url_entries" not in st.session_state:
    st.session_state.url_entries = [{"url": "", "criteria": [], "status": "idle"}]

# None = обработка не запущена; иначе — индекс текущей обрабатываемой ссылки.
# Единственный общий отладочный браузер означает, что одновременно можно
# обрабатывать только одну ссылку — остальные ждут своей очереди.
if "processing_index" not in st.session_state:
    st.session_state.processing_index = None


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
            return True
        else:
            st.error(f"❌ {label} — ошибка")
            st.code(result.stderr)
            return False
    except Exception as e:
        st.error(f"Не удалось запустить {label}: {e}")
        return False

# ─────────────────────────────────────────────────────────────────────────────
with st.container():
    st.title("Локальный интерфейс")

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

    processing = st.session_state.processing_index is not None

    # ── Поля ввода для каждого URL (заблокированы во время обработки очереди,
    #    чтобы правки не расходились с уже запущенным пайплайном) ─────────────
    for i, entry in enumerate(entries):
        st.markdown(f"---\n#### URL №{i + 1}")
        entry["url"] = st.text_input(
            label=f"Ссылка {i + 1}:",
            value=entry["url"],
            placeholder="https://example.com",
            key=f"url_{i}",
            disabled=processing,
        )
        entry["criteria"] = st.multiselect(
            label=f"Критерии для URL {i + 1} (1–4):",
            options=[1, 2, 3, 4],
            default=entry["criteria"],
            placeholder="Нажмите чтобы увидеть выпадающий список",
            key=f"criteria_{i}",
            disabled=processing,
        )

    st.markdown("---")

    if not processing:
        # ── Кнопка «Добавить URL» ─────────────────────────────────────────────
        if st.button("➕ Добавить URL"):
            st.session_state.url_entries.append({"url": "", "criteria": [], "status": "idle"})
            st.rerun()

        # ── Кнопка «Удалить последний» (если больше одного) ──────────────────
        if count > 1 and st.button("➖ Удалить последний URL"):
            st.session_state.url_entries.pop()
            st.rerun()

        # ── Кнопка «Создать отчёт» — запускает очередь обработки ─────────────
        if st.button("Создать отчёт"):
            has_error = False
            for entry in entries:
                if not entry["url"]:
                    st.error("⚠️ Одна из ссылок пустая!")
                    has_error = True
                if not entry["criteria"]:
                    st.error("⚠️ Для одной из ссылок не выбраны критерии!")
                    has_error = True

            if not has_error:
                for entry in entries:
                    entry["status"] = "idle"
                st.session_state.processing_index = 0
                st.rerun()

    # ── Диспетчер обработки текущей ссылки в очереди ──────────────────────────
    if processing:
        idx = st.session_state.processing_index
        entry = entries[idx]
        url_input = entry["url"]
        criteria_str = ",".join(map(str, sorted(entry["criteria"])))

        current_dir = os.path.dirname(os.path.abspath(__file__))
        parent_dir = os.path.dirname(current_dir)
        parcer_path = os.path.join(parent_dir, "Back", "parcer.py")
        compiler_path = os.path.join(parent_dir, "Back", "compiler.py")
        word_path = os.path.join(current_dir, "word_redactor.py")

        st.markdown(f"### Обработка URL №{idx + 1}: `{url_input}`")
        status = entry["status"]

        if status == "idle":
            ok = run_script(
                f"Фаза 1 — парсинг (URL {idx + 1})",
                [sys.executable, parcer_path, url_input, criteria_str, "phase1"]
            )
            entry["status"] = "awaiting_scroll" if ok else "error"
            st.rerun()

        elif status == "awaiting_scroll":
            st.info(
                "Переключитесь на окно браузера, прокрутите страницу до НАЧАЛА "
                "описания товара так, чтобы оно было видно на экране, затем "
                "нажмите «Начало здесь»."
            )
            col1, col2 = st.columns(2)
            with col1:
                if st.button("▶️ Начало здесь", key=f"mark_start_{idx}"):
                    entry["status"] = "mark_start_clicked"
                    st.rerun()
            with col2:
                if st.button("⏭️ На этой странице нет описания", key=f"skip_{idx}"):
                    entry["status"] = "skip_clicked"
                    st.rerun()

        elif status == "mark_start_clicked":
            ok = run_script(
                f"Отметка начала описания (URL {idx + 1})",
                [sys.executable, parcer_path, url_input, criteria_str, "mark_start"]
            )
            entry["status"] = "awaiting_scroll_end" if ok else "error"
            st.rerun()

        elif status == "awaiting_scroll_end":
            st.info(
                "Теперь прокрутите страницу до КОНЦА описания (туда, где "
                "начинается следующий раздел — отзывы, похожие товары и т.п.), "
                "затем нажмите «Конец здесь»."
            )
            if st.button("🏁 Конец здесь", key=f"mark_end_{idx}"):
                entry["status"] = "mark_end_clicked"
                st.rerun()

        elif status == "mark_end_clicked":
            ok = run_script(
                f"Захват описания (URL {idx + 1})",
                [sys.executable, parcer_path, url_input, criteria_str, "mark_end"]
            )
            entry["status"] = "finishing" if ok else "error"
            st.rerun()

        elif status == "skip_clicked":
            ok = run_script(
                f"Пропуск описания (URL {idx + 1})",
                [sys.executable, parcer_path, url_input, criteria_str, "skip"]
            )
            entry["status"] = "finishing" if ok else "error"
            st.rerun()

        elif status == "finishing":
            ok = run_script(
                f"Создание JSON (URL {idx + 1})",
                [sys.executable, compiler_path, url_input, criteria_str]
            )
            ok = ok and run_script(
                f"Создание Word (URL {idx + 1})",
                [sys.executable, word_path, url_input, criteria_str]
            )
            if ok:
                entry["status"] = "done"
                next_idx = idx + 1
                st.session_state.processing_index = next_idx if next_idx < count else None
            else:
                entry["status"] = "error"
            st.rerun()

        elif status == "error":
            st.error(f"Обработка URL №{idx + 1} остановлена из-за ошибки (см. лог выше).")
            if st.button("🔁 Повторить с фазы 1", key=f"retry_{idx}"):
                entry["status"] = "idle"
                st.rerun()

    # ── Итоги по уже обработанным ссылкам ─────────────────────────────────────
    for i, entry in enumerate(entries):
        if entry.get("status") == "done":
            st.success(f"✅ URL №{i + 1} обработан.")
