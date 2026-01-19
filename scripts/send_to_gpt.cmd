@echo off
setlocal enabledelayedexpansion

REM ============================================================
REM Send message to GPT via Friend Bridge (through SSH tunnel)
REM
REM Usage: send_to_gpt.cmd "Your message here"
REM Requires: friend_chat_tunnel.cmd running in another window
REM ============================================================

if "%~1"=="" (
    echo Usage: send_to_gpt.cmd "Your message"
    echo.
    echo Example: send_to_gpt.cmd "Analyze the current market data"
    exit /b 1
)

set "MESSAGE=%~1"
set "BRIDGE_URL=http://127.0.0.1:18765"

REM Load token from environment or secrets file
if "%FRIEND_BRIDGE_TOKEN%"=="" (
    for /f "tokens=1,* delims==" %%a in ('type "C:\secrets\hope\.env" 2^>nul ^| findstr "FRIEND_BRIDGE_TOKEN"') do set "FRIEND_BRIDGE_TOKEN=%%b"
)

if "%FRIEND_BRIDGE_TOKEN%"=="" (
    echo ERROR: FRIEND_BRIDGE_TOKEN not set
    echo Set it in environment or in C:\secrets\hope\.env
    exit /b 1
)

echo Sending to GPT: %MESSAGE:~0,50%...
echo.

curl -s -X POST "%BRIDGE_URL%/send" ^
    -H "Content-Type: application/json" ^
    -H "X-HOPE-Token: %FRIEND_BRIDGE_TOKEN%" ^
    -d "{\"to\": \"gpt\", \"message\": \"%MESSAGE%\", \"context\": \"friend_chat\"}"

echo.
