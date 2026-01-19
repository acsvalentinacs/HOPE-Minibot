@echo off
setlocal

REM ============================================================
REM Deploy HOPE minibot code to VPS
REM
REM Usage: deploy_to_vps.cmd [--restart]
REM   --restart: Also restart systemd services after deploy
REM
REM Deploys: core/*.py, scripts/*.py to VPS
REM Does NOT touch: ipc/, state/, .env (those stay on VPS)
REM ============================================================

set "KEY=C:\Users\kirillDev\.ssh\id_ed25519_hope"
set "HOST=root@46.62.232.161"
set "REMOTE_DIR=/opt/hope/minibot"
set "LOCAL_DIR=C:\Users\kirillDev\Desktop\TradingBot\minibot"

echo ============================================================
echo   HOPE Deploy to VPS
echo ============================================================
echo.
echo Local:  %LOCAL_DIR%
echo Remote: %HOST%:%REMOTE_DIR%
echo.

REM Deploy core modules
echo [1/3] Deploying core/*.py ...
scp -i "%KEY%" "%LOCAL_DIR%\core\friend_bridge_server.py" "%HOST%:%REMOTE_DIR%/core/"
scp -i "%KEY%" "%LOCAL_DIR%\core\gpt_bridge_runner.py" "%HOST%:%REMOTE_DIR%/core/"
scp -i "%KEY%" "%LOCAL_DIR%\core\chat_dispatch.py" "%HOST%:%REMOTE_DIR%/core/"
scp -i "%KEY%" "%LOCAL_DIR%\core\ipc_agent.py" "%HOST%:%REMOTE_DIR%/core/"

REM Deploy scripts
echo [2/3] Deploying scripts/*.py ...
scp -i "%KEY%" "%LOCAL_DIR%\scripts\set_cursor_atomic.py" "%HOST%:%REMOTE_DIR%/scripts/"

REM Check if --restart flag
if "%~1"=="--restart" (
    echo [3/3] Restarting services...
    ssh -i "%KEY%" %HOST% "systemctl restart friend-bridge gpt-bridge-runner && systemctl status friend-bridge gpt-bridge-runner --no-pager"
) else (
    echo [3/3] Skipping restart (use --restart flag to restart services)
)

echo.
echo ============================================================
echo   Deploy complete!
echo.
echo   To verify on VPS:
echo     ssh -i "%KEY%" %HOST%
echo     journalctl -u friend-bridge -u gpt-bridge-runner --since "5 min ago"
echo ============================================================

endlocal
