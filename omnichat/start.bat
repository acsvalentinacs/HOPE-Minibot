@echo off
REM === AI SIGNATURE ===
REM Created by: Claude (opus-4)
REM Created at: 2026-01-26T11:00:00Z
REM Purpose: HOPE OMNI-CHAT Launcher
REM === END SIGNATURE ===

title HOPE OMNI-CHAT v1.0

echo ============================================================
echo   HOPE OMNI-CHAT v1.0 - Trinity AI Chat
echo ============================================================
echo.

cd /d "%~dp0"

REM Keys loaded from C:\secrets\hope\.env (main secrets file)

REM Use project venv if available
set VENV_PYTHON=C:\Users\kirillDev\Desktop\TradingBot\.venv\Scripts\python.exe

if exist "%VENV_PYTHON%" (
    echo Using project venv...
    "%VENV_PYTHON%" -m pip install -q -r requirements.txt 2>nul
    echo Starting OMNI-CHAT...
    echo.
    "%VENV_PYTHON%" app.py
) else (
    echo Project venv not found, using system Python...
    python -m pip install -q -r requirements.txt 2>nul
    echo Starting OMNI-CHAT...
    echo.
    python app.py
)

echo.
echo ============================================================
echo   OMNI-CHAT closed.
echo ============================================================
pause
