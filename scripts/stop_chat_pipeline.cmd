@echo off
REM === AI SIGNATURE ===
REM Created by: Claude (opus-4)
REM Created at: 2026-01-23 21:00:00 UTC
REM === END SIGNATURE ===
REM
REM Stop HOPE Chat Pipeline processes

echo Stopping HOPE Chat Pipeline...

REM Kill by window title
taskkill /fi "windowtitle eq HOPE-GPTOrchestrator" /f 2>nul
taskkill /fi "windowtitle eq HOPE-ClaudeExecutor" /f 2>nul

REM Fallback: kill python processes running our modules
for /f "tokens=2" %%p in ('wmic process where "commandline like '%%gpt_orchestrator_runner%%'" get processid 2^>nul ^| findstr /r "[0-9]"') do (
    taskkill /pid %%p /f 2>nul
)
for /f "tokens=2" %%p in ('wmic process where "commandline like '%%claude_executor_runner%%'" get processid 2^>nul ^| findstr /r "[0-9]"') do (
    taskkill /pid %%p /f 2>nul
)

echo Done.
