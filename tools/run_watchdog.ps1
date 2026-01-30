# Run Process Watchdog with secrets loaded
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-01-30 19:15:00 UTC
# === END SIGNATURE ===

$ErrorActionPreference = "Stop"

# Load secrets
$envPath = "C:\secrets\hope.env"
if (Test-Path $envPath) {
    Get-Content $envPath | ForEach-Object {
        if ($_ -match '^([^#][^=]*)=(.*)$') {
            $key = $matches[1].Trim()
            $val = $matches[2].Trim()
            [Environment]::SetEnvironmentVariable($key, $val, 'Process')
        }
    }
    Write-Host "Secrets loaded" -ForegroundColor Green
}

# Change to project directory
Set-Location "C:\Users\kirillDev\Desktop\TradingBot\minibot"

# Create logs directory
New-Item -ItemType Directory -Path "logs" -Force | Out-Null

# Run process watchdog
Write-Host ""
Write-Host "Starting Process Watchdog..." -ForegroundColor Cyan
Write-Host "HTTP API: http://localhost:8080/" -ForegroundColor Yellow
Write-Host "=" * 60
python scripts/process_watchdog.py
