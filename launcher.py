import sys
import subprocess
import threading
import webbrowser
from pathlib import Path


def get_python():
    """Возвращает путь к python.exe — встроенному или системному"""
    embedded = Path(__file__).parent / "_python" / "python.exe"
    if embedded.exists():
        return str(embedded)
    return sys.executable


def get_streamlit_cmd(python_exe):
    """Возвращает команду для запуска streamlit.

    Намеренно не используем Scripts/streamlit.exe: это pip-генерируемый
    launcher-стаб (distlib), который сам вызывает CreateProcess для
    python.exe с путём, зашитым в заголовок exe в фиксированной кодировке.
    Если путь к проекту содержит не-ASCII символы (например, кириллицу),
    стаб падает с "Fatal error in launcher: Unable to create process".
    Поэтому всегда запускаем через _streamlit_runner.py — python.exe
    получает путь как обычный аргумент subprocess, без промежуточного стаба.
    """
    # Создаём вспомогательный скрипт рядом с launcher.py.
    # Он перебирает все известные точки входа streamlit разных версий
    # и в случае провала печатает диагностику.
    runner = Path(__file__).parent / "_streamlit_runner.py"
    runner.write_text(
        "import importlib, pkgutil, sys\n"
        "import streamlit\n"
        "print('Streamlit version:', getattr(streamlit, '__version__', '?'))\n"
        "print('Streamlit path:', streamlit.__file__)\n"
        "candidates = [\n"
        "    'streamlit.web.cli',      # >= 1.12\n"
        "    'streamlit.cli',          # < 1.12\n"
        "    'streamlit.web.bootstrap',\n"
        "    'streamlit.bootstrap',\n"
        "    'streamlit.__main__',\n"
        "]\n"
        "for name in candidates:\n"
        "    try:\n"
        "        mod = importlib.import_module(name)\n"
        "    except ImportError:\n"
        "        continue\n"
        "    if hasattr(mod, 'main'):\n"
        "        print('Using entry point:', name + '.main()')\n"
        "        mod.main()\n"
        "        sys.exit(0)\n"
        "print('ERROR: no streamlit CLI entry point found')\n"
        "print('Available submodules in streamlit:')\n"
        "for _, modname, ispkg in pkgutil.iter_modules(streamlit.__path__):\n"
        "    print('   ', modname, '(package)' if ispkg else '')\n"
        "sys.exit(1)\n",
        encoding="utf-8"
    )
    return [python_exe, str(runner)]


def main():
    print("=" * 50)
    print("  Запуск Streamlit приложения")
    print("=" * 50)

    python_exe = get_python()
    web_path = Path(__file__).parent / "word" / "web.py"

    if not web_path.exists():
        print(f"Ошибка: файл не найден {web_path}")
        input("\nНажмите Enter для выхода...")
        return

    try:
        print("Приложение запускается...")
        print("После запуска откройте браузер по адресу http://localhost:8501")
        print("Для остановки нажмите Ctrl+C")
        print("=" * 50)

        streamlit_cmd = get_streamlit_cmd(python_exe)
        proc = subprocess.Popen([*streamlit_cmd, "run", str(web_path),
                                  "--server.headless=true",
                                  "--browser.gatherUsageStats=false"])
        # Открываем браузер через 3 секунды — после старта сервера
        threading.Timer(1.0, lambda: webbrowser.open("http://localhost:8501")).start()
        proc.wait()
    except KeyboardInterrupt:
        print("\nПриложение остановлено пользователем")
    except Exception as e:
        print(f"Ошибка при запуске: {e}")
        input("\nНажмите Enter для выхода...")


if __name__ == "__main__":
    main()