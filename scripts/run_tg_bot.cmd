@echo off
setlocal

REM Force UTF-8 for Python console output (PowerShell/CMD compatibility)
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

title HOPE Telegram Bot
cd /d C:\Users\kirillDev\Desktop\TradingBot\minibot

echo === HOPE TELEGRAM BOT ===
echo Starting at %date% %time%
echo.

REM Use .venv Python from parent TradingBot folder
set "PYTHON=..\.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo ERROR: .venv not found at %cd%\%PYTHON%
    pause
    exit /b 1
)

:loop
"%PYTHON%" tg_bot_simple.py
echo.
echo Bot exited, restarting in 5 seconds...
timeout /t 5 /nobreak >nul
goto loop
