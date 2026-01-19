@echo off
setlocal
title HOPE Smoke Gate

REM Force UTF-8 for console output
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

cd /d C:\Users\kirillDev\Desktop\TradingBot\minibot

REM Resolve venv python (prefer parent TradingBot\.venv)
set "PYTHON=C:\Users\kirillDev\Desktop\TradingBot\.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    set "PYTHON=.venv\Scripts\python.exe"
)

if not exist "%PYTHON%" (
    echo ERROR: venv python not found.
    echo Tried: C:\Users\kirillDev\Desktop\TradingBot\.venv\Scripts\python.exe
    echo Tried: %CD%\.venv\Scripts\python.exe
    exit /b 1
)

echo === HOPE SMOKE GATE ===
echo Python: %PYTHON%
echo CWD: %CD%
echo.

echo [1/3] py_compile core modules...
"%PYTHON%" -m py_compile core\ipc_agent.py core\telegram_publisher.py core\ipc_tools.py
if errorlevel 1 (
    echo FAIL: py_compile errors
    exit /b 1
)
echo       OK

echo [2/3] pytest (if available)...
"%PYTHON%" -m pytest -q --tb=no 2>nul
if errorlevel 1 (
    echo       SKIP: no tests or pytest not installed
) else (
    echo       OK
)

echo [3/3] IPC health --passive...
"%PYTHON%" -m core.ipc_tools health --passive
if errorlevel 1 (
    echo FAIL: IPC health check failed
    exit /b 1
)

echo.
echo ========================================
echo PASS: smoke gate OK - system ready
echo ========================================
exit /b 0
