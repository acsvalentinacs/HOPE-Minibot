@echo off
chcp 65001 >nul
setlocal

title HOPE Friend Bridge

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

cd /d C:\Users\kirillDev\Desktop\TradingBot\minibot
set "PYTHON=C:\Users\kirillDev\Desktop\TradingBot\.venv\Scripts\python.exe"

echo ========================================
echo   HOPE Friend Bridge Server
echo   Port: 8765 (localhost only)
echo ========================================
echo.

%PYTHON% -m core.friend_bridge_server --port 8765

echo.
echo Friend Bridge stopped.
pause
