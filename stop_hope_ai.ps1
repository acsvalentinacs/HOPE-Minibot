# ═══════════════════════════════════════════════════════════════════════════
# HOPE AI - System Stop Script
# ═══════════════════════════════════════════════════════════════════════════

Write-Host "Stopping HOPE AI components..." -ForegroundColor Yellow

# Kill by port
$port8100 = Get-NetTCPConnection -LocalPort 8100 -ErrorAction SilentlyContinue
if ($port8100) {
    $port8100 | ForEach-Object {
        Write-Host "  Stopping PID $($_.OwningProcess)..." -ForegroundColor Gray
        Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
    }
}

# Kill Python processes with hope/ai_gateway in command line
Get-Process python -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -match "ai_gateway|moonbot_live|hope"
} | ForEach-Object {
    Write-Host "  Stopping $($_.ProcessName) (PID: $($_.Id))..." -ForegroundColor Gray
    Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
}

Write-Host "Done." -ForegroundColor Green
