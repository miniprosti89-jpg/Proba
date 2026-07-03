import os
import socket
import sys
import time
import subprocess
import threading
import webbrowser
from pathlib import Path

import requests

OLLAMA_MODEL = "qwen2.5:1.5b"
OLLAMA_DEFAULT_PORT = 11434
OLLAMA_FALLBACK_PORT = 11435


def get_ollama_exe():
    """Путь к портативному ollama.exe рядом с проектом, или None, если его нет."""
    exe = Path(__file__).parent / "Ollama" / "ollama.exe"
    return exe if exe.exists() else None


def is_port_in_use(host, port):
    """True, если на host:port уже что-то отвечает (например, чужая Ollama,
    установленная в корень системы)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0


def wait_for_ollama(base_url, timeout=30):
    """Ждёт, пока Ollama-сервер начнёт отвечать по base_url."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            requests.get(base_url, timeout=2)
            return True
        except Exception:
            time.sleep(1)
    return False


def start_ollama():
    """Запускает портативную Ollama (Ollama/ollama.exe) с моделями из
    Ollama_models/models и прогревает OLLAMA_MODEL в память.

    Если порт 11434 уже занят (например, на машине уже установлена и
    запущена системная Ollama в корень) — портативная копия поднимается
    на резервном порту (OLLAMA_HOST), чтобы гарантированно не задеть и не
    использовать чужой инстанс с чужими моделями.

    Возвращает (Popen, base_url) запущенного сервера, либо (None, None),
    если портативной Ollama нет или оба порта заняты — в этом случае
    приложение продолжает работать без LLM (шаги в parcer.py, использующие
    LLM, рассчитаны на такой фоллбэк).
    """
    ollama_exe = get_ollama_exe()
    if ollama_exe is None:
        print("Ollama не найдена в папке Ollama/ — пропускаем запуск LLM.")
        return None, None

    port = OLLAMA_DEFAULT_PORT
    env = os.environ.copy()

    if is_port_in_use("127.0.0.1", OLLAMA_DEFAULT_PORT):
        print(f"Порт {OLLAMA_DEFAULT_PORT} уже занят — похоже, на этой машине уже "
              f"запущена другая Ollama (например, установленная в корень системы).")
        port = OLLAMA_FALLBACK_PORT
        if is_port_in_use("127.0.0.1", port):
            print(f"Резервный порт {port} тоже занят — не удалось изолированно "
                  f"запустить портативную Ollama. Продолжаем без LLM.")
            return None, None
        env["OLLAMA_HOST"] = f"127.0.0.1:{port}"
        print(f"Портативная Ollama будет запущена отдельно, на порту {port}, "
              f"чтобы не использовать чужие модели.")

    base_url = f"http://localhost:{port}"

    models_dir = Path(__file__).parent / "Ollama_models" / "models"
    env["OLLAMA_MODELS"] = str(models_dir)

    log_path = ollama_exe.parent / "ollama.log"
    log_file = open(log_path, "w", encoding="utf-8", errors="replace")

    print(f"Запуск Ollama (порт {port})...")
    proc = subprocess.Popen(
        [str(ollama_exe), "serve"],
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )

    if not wait_for_ollama(base_url, timeout=30):
        print("Предупреждение: Ollama не ответила за 30с, продолжаем без прогрева модели.")
        return proc, base_url

    print(f"Прогрев модели {OLLAMA_MODEL}...")
    try:
        # Запрос без "prompt" — Ollama просто загружает модель в память и отвечает,
        # ничего не генерируя. Модель уже лежит локально в Ollama_models, поэтому
        # интернет для этого не нужен.
        # keep_alive: "30m" — держит модель в памяти дольше дефолтных 5 минут
        # простоя, синхронизировано с OLLAMA_KEEP_ALIVE в Back/parcer.py.
        requests.post(f"{base_url}/api/generate",
                      json={"model": OLLAMA_MODEL, "keep_alive": "30m"}, timeout=120)
        print("Модель загружена в память.")
    except Exception as e:
        print(f"Не удалось прогреть модель: {e}")

    return proc, base_url


def stop_ollama(proc, base_url=None):
    """Выгружает модель из памяти и останавливает Ollama-сервер."""
    if proc is None:
        return

    ollama_exe = get_ollama_exe()
    if ollama_exe is not None:
        env = os.environ.copy()
        if base_url:
            # "ollama stop" сам читает OLLAMA_HOST, чтобы понять, к какому
            # серверу обращаться — важно передать тот же порт, на котором
            # реально запущена наша портативная копия.
            env["OLLAMA_HOST"] = base_url.split("://", 1)[-1]
        try:
            subprocess.run([str(ollama_exe), "stop", OLLAMA_MODEL],
                            timeout=10, capture_output=True, env=env)
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

    ollama_proc, ollama_base_url = start_ollama()

    # Streamlit (и дальше Back/parcer.py, запускаемый из word/web.py) наследуют
    # окружение этого процесса, так как оба Popen-вызова идут без явного env=.
    # Поэтому достаточно один раз прописать OLLAMA_BASE_URL здесь — parcer.py
    # прочитает именно тот порт, на котором реально поднялась Ollama.
    if ollama_base_url:
        os.environ["OLLAMA_BASE_URL"] = ollama_base_url

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
        stop_ollama(ollama_proc, ollama_base_url)


if __name__ == "__main__":
    main()