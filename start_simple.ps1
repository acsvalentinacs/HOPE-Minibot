# HOPE AI - Simple Start Script
# Usage: .\start_simple.ps1

param(
    [ValidateSet("DRY", "TESTNET", "LIVE")]
    [string]$Mode = "DRY"
)

$ErrorActionPreference = "Stop"
$WorkDir = "C:\Users\kirillDev\Desktop\TradingBot\minibot"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  HOPE AI SYSTEM LAUNCHER" -ForegroundColor Cyan
Write-Host "  Mode: $Mode" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan

# Change to working directory
Set-Location $WorkDir

# Set environment
$env:AI_GATEWAY_MODE = $Mode
$env:AI_GATEWAY_PORT = "8100"

# Load secrets
$secretsPath = "C:\secrets\hope.env"
if (Test-Path $secretsPath) {
    Get-Content $secretsPath | ForEach-Object {
        if ($_ -match "^([^#][^=]*)=(.*)$") {
            [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim())
        }
    }
    Write-Host "[OK] Secrets loaded" -ForegroundColor Green
}

# Create directories
$dirs = @("state\ai", "state\ai\outcomes", "state\events", "logs")
foreach ($dir in $dirs) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
}
Write-Host "[OK] Directories ready" -ForegroundColor Green

# Check port 8100
$portInUse = Get-NetTCPConnection -LocalPort 8100 -ErrorAction SilentlyContinue
if ($portInUse) {
    Write-Host "[WARN] Port 8100 in use, stopping..." -ForegroundColor Yellow
    $portInUse | ForEach-Object {
        Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
}

# Start AI Gateway Server
Write-Host "`nStarting AI Gateway Server..." -ForegroundColor Yellow
$gateway = Start-Process python -ArgumentList "-m", "ai_gateway.server" -PassThru -WindowStyle Normal
Write-Host "[OK] AI Gateway PID: $($gateway.Id)" -ForegroundColor Green

# Wait for server
Write-Host "Waiting for server to start..." -ForegroundColor Gray
Start-Sleep -Seconds 5

# Health check
try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8100/health" -TimeoutSec 10
    Write-Host "[OK] Server responding: $($health.status)" -ForegroundColor Green
} catch {
    Write-Host "[WARN] Server may need more time to start" -ForegroundColor Yellow
}

# Summary
Write-Host "`n============================================================" -ForegroundColor Cyan
Write-Host "  HOPE AI LAUNCHED - Mode: $Mode" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "`nEndpoints:"
Write-Host "  Health:  http://127.0.0.1:8100/health"
Write-Host "  Status:  http://127.0.0.1:8100/status"
Write-Host "  Prices:  http://127.0.0.1:8100/price-feed/status"
Write-Host "`nTo stop: Stop-Process -Id $($gateway.Id)"
Write-Host "Or run:  .\stop_hope_ai.ps1"
