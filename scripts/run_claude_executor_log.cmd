@echo off
REM Run Claude Executor Runner with logging
cd /d C:\Users\kirillDev\Desktop\TradingBot\minibot
echo Starting executor at %date% %time% >> state\executor.log
python -m core.claude_executor_runner --poll-ms 500 >> state\executor.log 2>&1
