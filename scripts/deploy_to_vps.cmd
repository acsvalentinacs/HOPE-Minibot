@echo off
setlocal

REM ============================================================
REM Deploy HOPE minibot code to VPS via Git
REM
REM Usage: deploy_to_vps.cmd [--scp]
REM   (default): git push + VPS git pull + restart
REM   --scp: Direct scp (fallback if Git not set up)
REM
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

if "%~1"=="--scp" goto :SCP_DEPLOY

REM === Git-based deploy (default) ===
echo [1/3] Pushing to GitHub...
cd /d "%LOCAL_DIR%"
git push origin main
if errorlevel 1 (
    echo ERROR: git push failed. Use --scp for direct deploy.
    exit /b 1
)
echo.

echo [2/3] VPS: git pull + restart...
ssh -i "%KEY%" %HOST% "cd %REMOTE_DIR% && ./scripts/vps_pull_deploy.sh"
if errorlevel 1 (
    echo ERROR: VPS deploy failed.
    exit /b 1
)
echo.

echo [3/3] Verifying...
ssh -i "%KEY%" %HOST% "grep 'VERSION =' %REMOTE_DIR%/core/friend_bridge_server.py %REMOTE_DIR%/core/gpt_bridge_runner.py"

goto :END

:SCP_DEPLOY
REM === Direct SCP deploy (fallback) ===
echo [1/3] Deploying core/*.py via scp...
scp -i "%KEY%" "%LOCAL_DIR%\core\friend_bridge_server.py" "%HOST%:%REMOTE_DIR%/core/"
scp -i "%KEY%" "%LOCAL_DIR%\core\gpt_bridge_runner.py" "%HOST%:%REMOTE_DIR%/core/"
scp -i "%KEY%" "%LOCAL_DIR%\core\chat_dispatch.py" "%HOST%:%REMOTE_DIR%/core/"
scp -i "%KEY%" "%LOCAL_DIR%\core\ipc_agent.py" "%HOST%:%REMOTE_DIR%/core/"

echo [2/3] Deploying scripts...
scp -i "%KEY%" "%LOCAL_DIR%\scripts\set_cursor_atomic.py" "%HOST%:%REMOTE_DIR%/scripts/"
scp -i "%KEY%" "%LOCAL_DIR%\scripts\vps_pull_deploy.sh" "%HOST%:%REMOTE_DIR%/scripts/"

echo [3/3] Restarting services...
ssh -i "%KEY%" %HOST% "systemctl restart friend-bridge gpt-bridge-runner && systemctl status friend-bridge gpt-bridge-runner --no-pager"

:END
echo.
echo ============================================================
echo   Deploy complete!
echo.
echo   Verify:
echo     ssh -i "%KEY%" %HOST%
echo     journalctl -u friend-bridge -u gpt-bridge-runner --since "5 min ago"
echo ============================================================

endlocal
