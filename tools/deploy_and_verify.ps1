# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-02-04 01:15:00 UTC
# Purpose: Deploy dashboard to VPS and verify with SMOKE test
# === END SIGNATURE ===

param(
    [string]$VPS_HOST = "46.62.232.161",
    [string]$VPS_USER = "root",
    [string]$SSH_KEY = "$HOME\.ssh\id_ed25519_hope",
    [int]$DASHBOARD_PORT = 8080
)

$ErrorActionPreference = "Continue"
$LOCAL_PATH = "$PSScriptRoot\..\dashboard"

Write-Host "=======================================" -ForegroundColor Cyan
Write-Host "  HOPE DASHBOARD DEPLOY AND VERIFY" -ForegroundColor Cyan
Write-Host "=======================================" -ForegroundColor Cyan

# Step 1: SCP files
Write-Host "`n[1/4] Uploading files to VPS..." -ForegroundColor Yellow
scp -i $SSH_KEY "$LOCAL_PATH\dashboard_server.py" "${VPS_USER}@${VPS_HOST}:/opt/hope/minibot/dashboard/"
scp -i $SSH_KEY "$LOCAL_PATH\dashboard_v3_8k.html" "${VPS_USER}@${VPS_HOST}:/opt/hope/minibot/dashboard/hope_dashboard_8k.html"
Write-Host "  OK Files uploaded" -ForegroundColor Green

# Step 2: Restart dashboard
Write-Host "`n[2/4] Restarting dashboard server..." -ForegroundColor Yellow
ssh -i $SSH_KEY "${VPS_USER}@${VPS_HOST}" "pkill -f dashboard_server; sleep 2"
ssh -i $SSH_KEY "${VPS_USER}@${VPS_HOST}" "cd /opt/hope/minibot; nohup /opt/hope/venv/bin/python dashboard/dashboard_server.py --port $DASHBOARD_PORT > logs/dashboard.log 2>&1 &"
Start-Sleep -Seconds 4
Write-Host "  OK Dashboard restarted" -ForegroundColor Green

# Step 3: SMOKE test endpoints
Write-Host "`n[3/4] Running SMOKE tests..." -ForegroundColor Yellow
$endpoints = @("/api/status", "/api/processes", "/api/balances", "/api/allowlist", "/api/position")
$passCount = 0

foreach ($ep in $endpoints) {
    $code = ssh -i $SSH_KEY "${VPS_USER}@${VPS_HOST}" "curl -s -o /dev/null -w '%{http_code}' http://localhost:${DASHBOARD_PORT}${ep}"
    if ($code -eq "200") {
        Write-Host "  OK $ep -> 200" -ForegroundColor Green
        $passCount++
    } else {
        Write-Host "  X $ep -> $code" -ForegroundColor Red
    }
}

# Step 4: Process count
Write-Host "`n[4/4] Verifying processes..." -ForegroundColor Yellow
$json = ssh -i $SSH_KEY "${VPS_USER}@${VPS_HOST}" "curl -s http://localhost:${DASHBOARD_PORT}/api/processes"
$running = ($json | Select-String -Pattern '"state": "running"' -AllMatches).Matches.Count
Write-Host "  Running processes: $running/4" -ForegroundColor $(if ($running -ge 3) { "Green" } else { "Yellow" })

# Summary
Write-Host "`n=======================================" -ForegroundColor Cyan
if ($passCount -eq $endpoints.Count) {
    Write-Host "  SMOKE TEST: PASS ($passCount/$($endpoints.Count) OK)" -ForegroundColor Green
    Write-Host "  Dashboard: http://${VPS_HOST}:${DASHBOARD_PORT}/" -ForegroundColor Cyan
    exit 0
} else {
    Write-Host "  SMOKE TEST: FAIL" -ForegroundColor Red
    exit 1
}
