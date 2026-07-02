import os
import sys
import time
import subprocess
import threading
import webbrowser
from pathlib import Path

import requests

OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:1.5b"


def get_ollama_exe():
    """Путь к портативному ollama.exe рядом с проектом, или None, если его нет."""
    exe = Path(__file__).parent / "Ollama" / "ollama.exe"
    return exe if exe.exists() else None


def wait_for_ollama(timeout=30):
    """Ждёт, пока Ollama-сервер начнёт отвечать на localhost:11434."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            requests.get(OLLAMA_URL, timeout=2)
            return True
        except Exception:
            time.sleep(1)
    return False


def start_ollama():
    """Запускает портативную Ollama (Ollama/ollama.exe) с моделями из
    Ollama_models/models и прогревает OLLAMA_MODEL в память.

    Возвращает Popen запущенного сервера или None, если портативной
    Ollama нет — в этом случае приложение продолжает работать без LLM
    (шаги в parcer.py, использующие LLM, рассчитаны на такой фоллбэк).
    """
    ollama_exe = get_ollama_exe()
    if ollama_exe is None:
        print("Ollama не найдена в папке Ollama/ — пропускаем запуск LLM.")
        return None

    models_dir = Path(__file__).parent / "Ollama_models" / "models"
    env = os.environ.copy()
    env["OLLAMA_MODELS"] = str(models_dir)

    log_path = ollama_exe.parent / "ollama.log"
    log_file = open(log_path, "w", encoding="utf-8", errors="replace")

    print("Запуск Ollama...")
    proc = subprocess.Popen(
        [str(ollama_exe), "serve"],
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )

    if not wait_for_ollama(timeout=30):
        print("Предупреждение: Ollama не ответила за 30с, продолжаем без прогрева модели.")
        return proc

    print(f"Прогрев модели {OLLAMA_MODEL}...")
    try:
        # Запрос без "prompt" — Ollama просто загружает модель в память и отвечает,
        # ничего не генерируя. Модель уже лежит локально в Ollama_models, поэтому
        # интернет для этого не нужен.
        requests.post(f"{OLLAMA_URL}/api/generate", json={"model": OLLAMA_MODEL}, timeout=120)
        print("Модель загружена в память.")
    except Exception as e:
        print(f"Не удалось прогреть модель: {e}")

    return proc


def stop_ollama(proc):
    """Выгружает модель из памяти и останавливает Ollama-сервер."""
    if proc is None:
        return

    ollama_exe = get_ollama_exe()
    if ollama_exe is not None:
        try:
            subprocess.run([str(ollama_exe), "stop", OLLAMA_MODEL],
                            timeout=10, capture_output=True)
        except Exception:
            pass

    print("Остановка Ollama...")
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def get_python():
    """Возвращает путь к python.exe — встроенному или системному"""
    embedded = Path(__file__).parent / "_python" / "python.exe"
    if embedded.exists():
        return str(embedded)
    return sys.executable


def get_streamlit_cmd(python_exe):
    """Возвращает команду для запуска streamlit.

    Приоритет:
    1. Scripts/streamlit.exe — стандартный путь после pip install в embedded Python
    2. _streamlit_runner.py — маленький скрипт с try/except, работает
       и со старым (< 1.12, streamlit.cli) и с новым (>= 1.12, streamlit.web.cli).
    """
    python_path = Path(python_exe)
    streamlit_exe = python_path.parent / "Scripts" / "streamlit.exe"
    if streamlit_exe.exists():
        return [str(streamlit_exe)]

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

    ollama_proc = start_ollama()

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
    finally:
        stop_ollama(ollama_proc)


if __name__ == "__main__":
    main()