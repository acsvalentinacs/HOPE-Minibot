@echo off
setlocal

REM ============================================================
REM Friend Chat SSH Tunnel - connects Windows to VPS Friend Bridge
REM
REM Usage: Run this script, keep window open while using Friend Chat
REM Then use: curl http://127.0.0.1:8765/healthz
REM Or: python -m core.friend_bridge_cli health
REM ============================================================

set "KEY=C:\Users\kirillDev\.ssh\id_ed25519_hope"
set "HOST=root@46.62.232.161"
set "LOCAL_PORT=18765"
set "REMOTE_HOST=127.0.0.1"
set "REMOTE_PORT=8765"

echo ============================================================
echo   HOPE Friend Chat SSH Tunnel
echo ============================================================
echo.
echo Opening tunnel: localhost:%LOCAL_PORT% -^> VPS Friend Bridge
echo.
echo Keep this window OPEN while using Friend Chat.
echo Press Ctrl+C to close tunnel.
echo.
echo After tunnel is open, test with:
echo   curl http://127.0.0.1:18765/healthz
echo ============================================================
echo.

ssh -i "%KEY%" -N -L %LOCAL_PORT%:%REMOTE_HOST%:%REMOTE_PORT% %HOST%

echo.
echo Tunnel closed.
pause
