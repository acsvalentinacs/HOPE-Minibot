@echo off
REM Run GPT Orchestrator in AI mode
set AI_MODE=AI
cd /d C:\Users\kirillDev\Desktop\TradingBot\minibot
python -m core.gpt_orchestrator_runner --poll-ms 500
