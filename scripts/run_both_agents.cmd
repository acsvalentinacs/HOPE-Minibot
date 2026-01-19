@echo off
setlocal
title HOPE IPC Dual Agent Launcher
cd /d C:\Users\kirillDev\Desktop\TradingBot\minibot

echo === HOPE IPC DUAL AGENT LAUNCHER ===
echo Starting at %date% %time%
echo.

start "HOPE Claude Agent" cmd /c scripts\run_claude_agent.cmd
timeout /t 2 /nobreak >nul
start "HOPE GPT Agent" cmd /c scripts\run_gpt_agent.cmd

echo Both agents launched in separate windows.
echo Press any key to exit this launcher...
pause >nul
