@echo off
chcp 65001 >nul
title Streamlit App Launcher
cd /d "%~dp0"

echo ====================================
echo  Streamlit App Launcher
echo ====================================
echo.

set PYTHON=%~dp0_python\python.exe

:: Проверяем что _python\python.exe существует
if not exist "%PYTHON%" (
    echo [ОШИБКА] Папка _python не найдена!
    echo.
    echo Скачайте Python Embeddable Package с https://www.python.org/downloads/windows/
    echo Выберите "Windows embeddable package" для вашей архитектуры
    echo Распакуйте содержимое в папку _python рядом с этим файлом
    echo.
    pause
    exit /b 1
)

:: Шаг 1 — исправляем .pth файл через Python (надёжнее чем PowerShell)
echo [1/3] Настройка Python...
"%PYTHON%" -c ^
    "import glob, os; files = glob.glob(os.path.dirname(r'%PYTHON%') + '/python3*._pth'); [open(f,'w',encoding='utf-8').write(open(f,encoding='utf-8').read().replace('# import site','import site')) for f in files]; print('PTH исправлен:', files)"

:: Шаг 2 — устанавливаем pip если нет
"%PYTHON%" -c "import pip" >nul 2>&1
if errorlevel 1 (
    echo [2/3] Устанавливаю pip...
    if not exist "%~dp0_python\get-pip.py" (
        curl -sS -o "%~dp0_python\get-pip.py" https://bootstrap.pypa.io/get-pip.py
        if errorlevel 1 (
            echo [ОШИБКА] Не удалось скачать get-pip.py. Проверьте интернет.
            pause & exit /b 1
        )
    )
    "%PYTHON%" "%~dp0_python\get-pip.py" --no-warn-script-location
    if errorlevel 1 (
        echo [ОШИБКА] Не удалось установить pip.
        pause & exit /b 1
    )
    echo pip установлен успешно.
) else (
    echo [2/3] pip уже установлен.
)

:: Шаг 3 — устанавливаем зависимости если streamlit ещё не установлен
"%PYTHON%" -c "import streamlit" >nul 2>&1
if errorlevel 1 (
    echo [3/3] Устанавливаю зависимости из requirements.txt...
    echo Это может занять долгое время при первом запуске...
    "%PYTHON%" -m pip install -r "%~dp0requirements.txt" --no-warn-script-location
    if errorlevel 1 (
        echo [ОШИБКА] Не удалось установить зависимости.
        pause & exit /b 1
    )
    echo Зависимости установлены.
) else (
    echo [3/3] Зависимости уже установлены.
)

:: Запускаем приложение
echo.
echo Запуск приложения...
echo Откройте браузер: http://localhost:8501
echo Остановить: Ctrl+C
echo ====================================
"%PYTHON%" launcher.py

if errorlevel 1 (
    echo.
    echo [ОШИБКА] Приложение завершилось с ошибкой.
    pause
)