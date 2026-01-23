@echo off
REM === AI SIGNATURE ===
REM Created by: Claude (opus-4)
REM Created at: 2026-01-23 21:00:00 UTC
REM === END SIGNATURE ===
REM
REM HOPE Chat Pipeline - runs GPT Orchestrator + Claude Executor
REM For Task Scheduler (no pause, auto-restart on crash)

cd /d C:\Users\kirillDev\Desktop\TradingBot\minibot

REM Load secrets from .env
set DOTENV_PATH=C:\secrets\hope\.env
if exist "%DOTENV_PATH%" (
    for /f "usebackq tokens=1,* delims==" %%a in ("%DOTENV_PATH%") do (
        set "line=%%a"
        if not "!line:~0,1!"=="#" if not "%%a"=="" set "%%a=%%b"
    )
)

REM Enable delayed expansion for loop
setlocal EnableDelayedExpansion

REM Start GPT Orchestrator (background window)
echo [%date% %time%] Starting GPT Orchestrator...
start "HOPE-GPTOrchestrator" /min cmd /c "python -m core.gpt_orchestrator_runner --poll-ms 2000"

REM Wait 3 seconds
timeout /t 3 /nobreak >nul

REM Start Claude Executor (background window)
echo [%date% %time%] Starting Claude Executor...
start "HOPE-ClaudeExecutor" /min cmd /c "python -m core.claude_executor_runner --poll-ms 2000"

echo [%date% %time%] Chat pipeline started.
echo GPT Orchestrator: HOPE-GPTOrchestrator window
echo Claude Executor: HOPE-ClaudeExecutor window
