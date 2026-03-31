@echo off
chcp 65001 >nul
title Streamlit App Launcher
cd /d "%~dp0"

echo ====================================
echo  Streamlit App Launcher
echo ====================================
echo.

set PYTHON=%~dp0_python\python.exe


if not exist "%PYTHON%" (
    echo [ERROR] _python folder not found!
    echo.
    echo Please download Python Embeddable Package from:
    echo https://www.python.org/downloads/windows/
    echo Choose "Windows embeddable package" for your architecture
    echo Extract contents into _python folder next to this file
    echo.
    pause
    exit /b 1
)

:: Fix .pth file to enable import site
echo [1/3] Configuring Python...
"%PYTHON%" -c "import glob, os; files = glob.glob(os.path.dirname(r'%PYTHON%') + '/python3*._pth'); [open(f,'w',encoding='utf-8').write(open(f,encoding='utf-8').read().replace('# import site','import site')) for f in files]; print('PTH fixed:', files)"

:: Install pip if missing
"%PYTHON%" -c "import pip" >nul 2>&1
if errorlevel 1 (
    echo [2/3] Installing pip...
    if not exist "%~dp0_python\get-pip.py" (
        curl -sS -o "%~dp0_python\get-pip.py" https://bootstrap.pypa.io/get-pip.py
        if errorlevel 1 (
            echo [ERROR] Failed to download get-pip.py. Check internet connection.
            pause & exit /b 1
        )
    )
    "%PYTHON%" "%~dp0_python\get-pip.py" --no-warn-script-location
    if errorlevel 1 (
        echo [ERROR] Failed to install pip.
        pause & exit /b 1
    )
    echo pip installed successfully.
) else (
    echo [2/3] pip already installed.
)

:: Install dependencies if streamlit is missing
"%PYTHON%" -c "import streamlit" >nul 2>&1
if errorlevel 1 (
    echo [3/3] Installing dependencies from requirements.txt...
    echo This may take a long time on first run...
    "%PYTHON%" -m pip install -r "%~dp0requirements.txt" --no-warn-script-location
    if errorlevel 1 (
        echo [ERROR] Failed to install dependencies.
        pause & exit /b 1
    )
    echo Dependencies installed successfully.
) else (
    echo [3/3] Dependencies already installed.
)



:: Launch the app
echo.
echo Starting app...
echo Open browser at: http://localhost:8501
echo Press Ctrl+C to stop
echo ====================================
"%PYTHON%" launcher.py

if errorlevel 1 (
    echo.
    echo [ERROR] App exited with an error.
    pause
)