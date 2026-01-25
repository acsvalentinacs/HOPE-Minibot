@echo off
REM === AI SIGNATURE ===
REM Created by: Claude (opus-4)
REM Created at (UTC): 2026-01-25T16:45:00Z
REM Purpose: News Spider launcher for Task Scheduler
REM === END SIGNATURE ===

REM HOPE News Spider Auto-Runner
REM Run via Task Scheduler every 15-30 minutes

cd /d C:\Users\kirillDev\Desktop\TradingBot\minibot

REM Check STOP flag
if exist "C:\Users\kirillDev\Desktop\TradingBot\flags\STOP.flag" (
    echo [%DATE% %TIME%] STOP.flag detected - skipping spider run
    exit /b 0
)

REM Activate venv and run spider bridge (collect + publish)
call .venv\Scripts\activate.bat

REM Run in LENIENT mode with dry-run by default
REM Remove --dry-run for production
python -m core.spider.telegram_bridge --collect --mode lenient --dry-run 2>&1 >> logs\spider_auto.log

REM Deactivate
deactivate

exit /b 0
