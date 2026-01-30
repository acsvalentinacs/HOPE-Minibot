# HOPE AI - Production Trading Launcher
# Created by Claude (opus-4.5) at 2026-01-30 10:30:00 UTC

param(
    [ValidateSet("DRY", "TESTNET", "LIVE")]
    [string]$Mode = "TESTNET",
    [switch]$Confirm,
    [double]$PositionSize = 10.0
)

$ErrorActionPreference = "Stop"
Set-Location "C:\Users\kirillDev\Desktop\TradingBot\minibot"

Write-Host "=" * 60 -ForegroundColor Cyan
Write-Host "  HOPE AI Production Trading System" -ForegroundColor Cyan
Write-Host "=" * 60 -ForegroundColor Cyan
Write-Host ""
Write-Host "  Mode: $Mode" -ForegroundColor Yellow
Write-Host "  Position Size: $$PositionSize" -ForegroundColor Yellow
Write-Host ""

# Load secrets
if (Test-Path "C:\secrets\hope.env") {
    Get-Content "C:\secrets\hope.env" | ForEach-Object {
        if ($_ -match "^([^#=]+)=(.*)$") {
            [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim().Trim('"'))
        }
    }
    Write-Host "[OK] Loaded secrets" -ForegroundColor Green
}

# Build command
$cmd = "python scripts/run_production.py --mode $Mode --position-size $PositionSize"

if ($Confirm) {
    $cmd += " --confirm"
}

Write-Host ""
Write-Host "Starting: $cmd" -ForegroundColor Gray
Write-Host ""

# Execute
Invoke-Expression $cmd
