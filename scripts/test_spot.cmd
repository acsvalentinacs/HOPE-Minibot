@echo off
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

cd /d C:\Users\kirillDev\Desktop\TradingBot\minibot
set "PYTHON=C:\Users\kirillDev\Desktop\TradingBot\.venv\Scripts\python.exe"

echo Testing SPOT Testnet client...
%PYTHON% -c "from core.spot_testnet_client import SpotTestnetClient; import json; c = SpotTestnetClient(); r = c.health_check(); open('state/spot_health.json', 'w').write(json.dumps(r, indent=2)); print('Result saved to state/spot_health.json')"

echo.
echo Done.
