# Run pump_detector with secrets loaded
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-01-30 19:00:00 UTC
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
            Write-Host "Loaded: $key"
        }
    }
}

# Change to project directory
Set-Location "C:\Users\kirillDev\Desktop\TradingBot\minibot"

# Run pump_detector
Write-Host ""
Write-Host "Starting pump_detector --top 10..."
Write-Host "=" * 60
python scripts/pump_detector.py --top 10
