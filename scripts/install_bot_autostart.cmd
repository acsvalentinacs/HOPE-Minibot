@echo off
REM === AI SIGNATURE ===
REM Created by: Claude (opus-4)
REM Created at: 2026-01-26T07:45:00Z
REM Purpose: Install Telegram Bot autostart task
REM === END SIGNATURE ===

echo ============================================================
echo  HOPE Telegram Bot - Autostart Installer
echo ============================================================
echo.

cd /d "C:\Users\kirillDev\Desktop\TradingBot\minibot"

REM Delete existing task if any
schtasks /delete /tn "HOPE\TelegramBot" /f >nul 2>&1

REM Create task using schtasks command (no XML needed)
schtasks /create /tn "HOPE\TelegramBot" /tr "C:\Users\kirillDev\Desktop\TradingBot\minibot\scripts\start_tg_bot.cmd" /sc onlogon /delay 0000:45 /rl limited

if %errorlevel% equ 0 (
    echo.
    echo ============================================================
    echo  SUCCESS! Bot will auto-start on logon.
    echo ============================================================
    echo.
    echo To start bot NOW, run:
    echo   schtasks /run /tn "HOPE\TelegramBot"
    echo.
) else (
    echo.
    echo ERROR: Failed to create task.
    echo Try running this script as Administrator.
    echo.
)

pause
