@echo off
REM === AI SIGNATURE ===
REM Created by: Claude (opus-4)
REM Created at: 2026-01-26T07:30:00Z
REM Purpose: Start Telegram bot (single instance)
REM === END SIGNATURE ===

title HOPE TG Bot v2.3

echo ============================================================
echo   HOPE Telegram Bot v2.3.0
echo ============================================================
echo.

cd /d "C:\Users\kirillDev\Desktop\TradingBot\minibot"

REM Kill existing bot instances
echo Stopping old instances...
for /f "tokens=2" %%i in ('wmic process where "commandline like '%%tg_bot_simple%%'" get processid 2^>nul ^| findstr /r "[0-9]"') do (
    taskkill /F /PID %%i >nul 2>&1
)
timeout /t 2 /nobreak >nul

echo Starting bot...
echo.

"C:\Users\kirillDev\Desktop\TradingBot\.venv\Scripts\python.exe" tg_bot_simple.py

REM If bot crashes, show error
echo.
echo ============================================================
echo   Bot stopped. Press any key to restart or close window.
echo ============================================================
pause
goto :start_bot

:start_bot
"C:\Users\kirillDev\Desktop\TradingBot\.venv\Scripts\python.exe" tg_bot_simple.py
goto :eof
