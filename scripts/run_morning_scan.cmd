@echo off
chcp 65001 >nul
echo === HOPE MORNING SCAN ===
echo Starting at %date% %time%

cd /d "C:\Users\kirillDev\Desktop\TradingBot\minibot"
"C:\Users\kirillDev\Desktop\TradingBot\.venv\Scripts\python.exe" "C:\Users\kirillDev\Desktop\TradingBot\minibot\scripts\morning_scan.py"

echo Exit code: %ERRORLEVEL%
