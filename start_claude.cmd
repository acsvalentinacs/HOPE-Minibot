@echo off
REM ============================================================
REM  CLAUDE CODE LAUNCHER - NO PERMISSION DIALOGS
REM  Created: 2026-01-21
REM
REM  USAGE: Double-click or run from any location
REM  Result: Claude starts in minibot folder with bypass mode
REM ============================================================

REM Change to minibot directory FIRST
cd /d "C:\Users\kirillDev\Desktop\TradingBot\minibot"

REM Show info
echo.
echo === CLAUDE LAUNCHER ===
echo Directory: %CD%
echo Mode: bypass permissions
echo.

REM Launch Claude with bypass flag from correct directory
claude --dangerously-skip-permissions

REM Handle errors
if errorlevel 1 (
    echo.
    echo [ERROR] Claude exited with code %errorlevel%
    pause
)
