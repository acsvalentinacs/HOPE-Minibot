# HOPE AI Dashboard Launcher
# Created: 2026-01-31 04:20 UTC

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  HOPE AI Trading Dashboard" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# Check dependencies
Write-Host "Checking dependencies..." -ForegroundColor Yellow
python -c "import aiohttp, aiohttp_cors" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing aiohttp..." -ForegroundColor Yellow
    pip install aiohttp aiohttp-cors
}

# Start dashboard server
Write-Host ""
Write-Host "Starting Dashboard Server on http://localhost:8080" -ForegroundColor Green
Write-Host "Press Ctrl+C to stop" -ForegroundColor Yellow
Write-Host ""

cd C:\Users\kirillDev\Desktop\TradingBot\minibot
python dashboard/dashboard_server.py --port 8080
