@echo off
setlocal

REM Force UTF-8 for Python console output (PowerShell/CMD compatibility)
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

title HOPE IPC Agent - Claude
cd /d C:\Users\kirillDev\Desktop\TradingBot\minibot

echo === HOPE IPC CLAUDE AGENT ===
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
"%PYTHON%" core\ipc_agent.py --role=claude --poll_sec=2
echo.
echo Agent exited, restarting in 5 seconds...
timeout /t 5 /nobreak >nul
goto loop
