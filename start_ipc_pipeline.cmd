@echo off
REM === HOPE IPC Pipeline Startup Script ===
REM Starts: Telegram Bot + GPT Orchestrator + Claude Executor
REM
REM Created by: Claude
REM Created at: 2026-01-20

echo ============================================
echo HOPE IPC Pipeline Startup
echo ============================================
echo.

cd /d "C:\Users\kirillDev\Desktop\TradingBot\minibot"

REM Load .env
set DOTENV_PATH=C:\secrets\hope\.env
for /f "usebackq tokens=1,2 delims==" %%a in ("%DOTENV_PATH%") do (
    if not "%%a"=="" if not "%%a:~0,1%"=="#" set "%%a=%%b"
)

echo AI_MODE=%AI_MODE%
echo.

REM Check if processes already running
tasklist /fi "windowtitle eq HOPE-TelegramBot" 2>nul | find "python" >nul
if %errorlevel%==0 (
    echo [WARN] Telegram Bot already running!
) else (
    echo [1/3] Starting Telegram Bot...
    start "HOPE-TelegramBot" cmd /c "python -m integrations.telegram_bot"
    timeout /t 2 /nobreak >nul
)

tasklist /fi "windowtitle eq HOPE-GPTOrchestrator" 2>nul | find "python" >nul
if %errorlevel%==0 (
    echo [WARN] GPT Orchestrator already running!
) else (
    echo [2/3] Starting GPT Orchestrator...
    start "HOPE-GPTOrchestrator" cmd /c "python -m core.gpt_orchestrator_runner"
    timeout /t 2 /nobreak >nul
)

tasklist /fi "windowtitle eq HOPE-ClaudeExecutor" 2>nul | find "python" >nul
if %errorlevel%==0 (
    echo [WARN] Claude Executor already running!
) else (
    echo [3/3] Starting Claude Executor...
    start "HOPE-ClaudeExecutor" cmd /c "python -m core.claude_executor_runner"
    timeout /t 2 /nobreak >nul
)

echo.
echo ============================================
echo All processes started!
echo.
echo Windows opened:
echo   - HOPE-TelegramBot
echo   - HOPE-GPTOrchestrator
echo   - HOPE-ClaudeExecutor
echo.
echo Test with: /ask какая погода?
echo ============================================
pause
