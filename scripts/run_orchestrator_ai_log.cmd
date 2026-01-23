@echo off
REM Run GPT Orchestrator in AI mode with logging
set AI_MODE=AI
cd /d C:\Users\kirillDev\Desktop\TradingBot\minibot
echo Starting orchestrator at %date% %time% >> state\orchestrator.log
python -m core.gpt_orchestrator_runner --poll-ms 500 >> state\orchestrator.log 2>&1
