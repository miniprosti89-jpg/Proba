import sys
import subprocess
from pathlib import Path


def get_python():
    """Возвращает путь к python.exe — встроенному или системному"""
    embedded = Path(__file__).parent / "_python" / "python.exe"
    if embedded.exists():
        return str(embedded)
    return sys.executable


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

        subprocess.run([python_exe, "-m", "streamlit", "run", str(web_path)])
    except KeyboardInterrupt:
        print("\nПриложение остановлено пользователем")
    except Exception as e:
        print(f"Ошибка при запуске: {e}")
        input("\nНажмите Enter для выхода...")


if __name__ == "__main__":
    main()