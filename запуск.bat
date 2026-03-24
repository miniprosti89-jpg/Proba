@echo off
title Streamlit App Launcher
cd /d "%~dp0"

echo ====================================
echo  Streamlit App Launcher
echo ====================================

py --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed!
    echo.
    echo Please install Python from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation
    echo.
    pause
    exit /b 1
)


py launcher.py