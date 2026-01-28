<#
=== AI SIGNATURE ===
Created by: Claude (opus-4)
Created at: 2026-01-28T16:55:00Z
Purpose: Start HOPE TG Bot with watchdog supervisor
=== END SIGNATURE ===
#>

param(
    [switch]$NoWatchdog
)

$ErrorActionPreference = "Stop"
$BOT_DIR = "C:\Users\kirillDev\Desktop\TradingBot\minibot"
$VENV_PYTHON = "C:\Users\kirillDev\Desktop\TradingBot\.venv\Scripts\python.exe"
$LOCK_DIR = "C:\Users\kirillDev\Desktop\TradingBot\state\pids"

Write-Host "============================================" -ForegroundColor Cyan
Write-Host " HOPE TG Bot Starter (Stable v2)" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

# 1. Kill existing
Write-Host "`n[1/4] Stopping existing processes..." -ForegroundColor Yellow
Get-Process python*, python3* -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

# 2. Clean locks
Write-Host "[2/4] Cleaning locks..." -ForegroundColor Yellow
Remove-Item "$LOCK_DIR\tg_bot_simple.lock" -Force -ErrorAction SilentlyContinue
Remove-Item "$BOT_DIR\state\health_tgbot.json" -Force -ErrorAction SilentlyContinue

# 3. Verify
Write-Host "[3/4] Verifying Python..." -ForegroundColor Yellow
if (-not (Test-Path $VENV_PYTHON)) {
    Write-Host "  ERROR: Python not found" -ForegroundColor Red
    exit 1
}

# 4. Start
Write-Host "[4/4] Starting..." -ForegroundColor Yellow

if ($NoWatchdog) {
    Write-Host "  Mode: DIRECT (no watchdog)" -ForegroundColor Gray
    $proc = Start-Process -FilePath $VENV_PYTHON `
        -ArgumentList "-u", "$BOT_DIR\tg_bot_simple.py" `
        -WorkingDirectory $BOT_DIR `
        -PassThru `
        -WindowStyle Hidden `
        -RedirectStandardError "$BOT_DIR\logs\bot_err.log"
    Write-Host "  Bot PID: $($proc.Id)" -ForegroundColor Green
} else {
    Write-Host "  Mode: WATCHDOG SUPERVISED" -ForegroundColor Green
    $proc = Start-Process -FilePath $VENV_PYTHON `
        -ArgumentList "-u", "$BOT_DIR\tools\tgbot_watchdog.py" `
        -WorkingDirectory $BOT_DIR `
        -PassThru
    Write-Host "  Watchdog PID: $($proc.Id)" -ForegroundColor Green
}

# Wait for heartbeat
Write-Host "`nWaiting for heartbeat..." -ForegroundColor Yellow
$HEALTH_FILE = "$BOT_DIR\state\health_tgbot.json"

for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Path $HEALTH_FILE) {
        try {
            $health = Get-Content $HEALTH_FILE -Raw | ConvertFrom-Json
            if ($health.hb_ts) {
                Write-Host "  Heartbeat OK: $($health.hb_ts)" -ForegroundColor Green
                break
            }
        } catch {}
    }
    Write-Host "  Waiting... ($($i+1)/15)" -ForegroundColor Gray
}

Write-Host "`n============================================" -ForegroundColor Cyan
Write-Host " Bot started! Test: /panel in Telegram" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Cyan
