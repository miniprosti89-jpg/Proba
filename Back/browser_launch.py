"""Общий код запуска/переиспользования отладочного браузера.

И launcher.py (открывает Streamlit), и parcer.py (открывает страницы товаров)
используют один и тот же порт отладки и профиль — благодаря этому все вкладки
(Streamlit и открываемые товары) оказываются в одном и том же окне браузера,
а не в разных, никак не связанных друг с другом процессах.
"""
import os
import time
import subprocess

import requests

DEBUG_PORT = "9222"

if os.name == "posix":
    USER_DATA_DIR = "/tmp/playwright-profile"
else:
    # Один и тот же профиль на все запуски — сохраняет куки и сессию
    # сайта между запусками (иначе каждый заход выглядит первым).
    USER_DATA_DIR = r"C:\Temp\chrome-debug"

_BROWSER_PATHS = [
    # Google Chrome
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Chromium\Application\chrome.exe"),
    # Microsoft Edge (Chromium) — на Windows обычно предустановлен
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    # Яндекс.Браузер (Chromium)
    os.path.expandvars(r"%LOCALAPPDATA%\Yandex\YandexBrowser\Application\browser.exe"),
    r"C:\Program Files\Yandex\YandexBrowser\Application\browser.exe",
    r"C:\Program Files (x86)\Yandex\YandexBrowser\Application\browser.exe",
]


def find_browser_executable():
    """Путь к поддерживаемому браузеру (Chrome/Edge/Яндекс), либо None."""
    if os.name == "posix":
        return "chromium"  # предполагается, что chromium установлен в PATH
    for path in _BROWSER_PATHS:
        if os.path.exists(path):
            return path
    return None


def is_debug_browser_running():
    """True, если отладочный порт уже отвечает (браузер уже запущен нами же)."""
    try:
        requests.get(f"http://localhost:{DEBUG_PORT}/json/version", timeout=1)
        return True
    except Exception:
        return False


def ensure_debug_browser_running(initial_url=None, wait_timeout=30):
    """Гарантирует, что отладочный браузер запущен на DEBUG_PORT.

    Если уже запущен — ничего не делает (initial_url в этом случае не
    используется; чтобы всё равно открыть вкладку, используйте Playwright
    connect_over_cdp + new_page самостоятельно).
    Если ещё не запущен — стартует его (опционально сразу с initial_url
    в первой вкладке) и ждёт, пока отладочный порт начнёт отвечать.

    Возвращает True, если браузер запущен (был уже, либо запустили сейчас).
    """
    if is_debug_browser_running():
        return True

    browser_path = find_browser_executable()
    if browser_path is None:
        raise FileNotFoundError(
            "Не найден ни один поддерживаемый браузер (Chrome/Edge/Яндекс). "
            "Проверьте пути в browser_launch._BROWSER_PATHS."
        )

    args = [
        browser_path,
        f"--remote-debugging-port={DEBUG_PORT}",
        f"--user-data-dir={USER_DATA_DIR}",
        "--start-maximized",
    ]
    if initial_url:
        args.append(initial_url)

    subprocess.Popen(args)

    deadline = time.time() + wait_timeout
    while time.time() < deadline:
        if is_debug_browser_running():
            return True
        time.sleep(1)
    return False
