@echo off
chcp 65001 >nul
title Streamlit App Launcher
cd /d "%~dp0"

echo ====================================
echo  Запуск Streamlit приложения
echo ====================================

python launcher.py

pause