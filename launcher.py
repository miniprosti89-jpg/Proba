import os
import sys
import subprocess
import importlib
from pathlib import Path


def install_package(package):
    """Установка отдельного пакета"""
    print(f"Устанавливаю {package}...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", package])


def install_from_requirements():
    """Установка всех зависимостей из requirements.txt"""
    req_file = Path(__file__).parent / "requirements.txt"

    if req_file.exists():
        print(f"Найден requirements.txt, устанавливаю зависимости...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", str(req_file)])
            return True
        except subprocess.CalledProcessError as e:
            print(f"Ошибка при установке из requirements.txt: {e}")
            return False
    else:
        print("requirements.txt не найден")
        return False


def check_and_install_dependencies():
    """Проверка и установка необходимых зависимостей"""

    # Сначала пробуем установить всё из requirements.txt
    if install_from_requirements():
        print("Все зависимости из requirements.txt установлены")
        return

    # Если requirements.txt нет или установка не удалась, устанавливаем вручную
    required = ['streamlit']

    # Пробуем прочитать requirements.txt если он существует
    req_file = Path(__file__).parent / "requirements.txt"
    if req_file.exists():
        with open(req_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    required.append(line.split('>=')[0].split('==')[0])

    # Устанавливаем недостающие пакеты
    for package in required:
        try:
            importlib.import_module(package)
            print(f"{package} уже установлен")
        except ImportError:
            install_package(package)


def main():
    """Основная функция запуска"""
    print("=" * 50)
    print("Запуск Streamlit приложения")
    print("=" * 50)

    # Проверяем и устанавливаем зависимости
    print("\n[1/2] Проверка зависимостей...")
    try:
        check_and_install_dependencies()
    except Exception as e:
        print(f"Ошибка при установке зависимостей: {e}")
        input("\nНажмите Enter для выхода...")
        return

    # Запускаем Streamlit приложение
    print("\n[2/2] Запуск приложения...")
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

        subprocess.run([sys.executable, "-m", "streamlit", "run", str(web_path)])
    except KeyboardInterrupt:
        print("\nПриложение остановлено пользователем")
    except Exception as e:
        print(f"Ошибка при запуске: {e}")
        input("\nНажмите Enter для выхода...")


if __name__ == "__main__":
    main()