# ═══════════════════════════════════════════════════════════════════════════
# HOPE AI - System Launch Script v1.0
# 
# Запускает всю систему в правильном порядке:
# 1. AI Gateway Server
# 2. MoonBot Live Integration
# 3. Price Feed Bridge
# 4. Monitor
#
# Usage: .\start_hope_ai.ps1 [-Mode DRY|TESTNET|LIVE] [-NoMonitor]
# ═══════════════════════════════════════════════════════════════════════════

param(
    [ValidateSet("DRY", "TESTNET", "LIVE")]
    [string]$Mode = "DRY",
    [switch]$NoMonitor
)

$ErrorActionPreference = "Stop"
$WorkDir = "C:\Users\kirillDev\Desktop\TradingBot\minibot"

Write-Host "═══════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  HOPE AI SYSTEM LAUNCHER v1.0" -ForegroundColor Cyan
Write-Host "  Mode: $Mode" -ForegroundColor $(if($Mode -eq "LIVE"){"Red"}elseif($Mode -eq "TESTNET"){"Yellow"}else{"Green"})
Write-Host "═══════════════════════════════════════════════════════════════" -ForegroundColor Cyan

# ───────────────────────────────────────────────────────────────────────────
# PRE-FLIGHT CHECKS
# ───────────────────────────────────────────────────────────────────────────

Write-Host "`n[1/5] Pre-flight checks..." -ForegroundColor Yellow

# Check working directory
if (-not (Test-Path $WorkDir)) {
    Write-Host "ERROR: Working directory not found: $WorkDir" -ForegroundColor Red
    exit 1
}
Set-Location $WorkDir

# Check Python
try {
    $pyVer = python --version 2>&1
    Write-Host "  ✓ Python: $pyVer" -ForegroundColor Green
} catch {
    Write-Host "  ✗ Python not found" -ForegroundColor Red
    exit 1
}

# Run diagnostic
Write-Host "  Running diagnostic..." -ForegroundColor Gray
$diagResult = python hope_diagnostic.py --json 2>&1 | ConvertFrom-Json
if ($diagResult.summary.broken -gt 0) {
    Write-Host "  ✗ BROKEN components detected: $($diagResult.summary.broken)" -ForegroundColor Red
    Write-Host "  Run: python hope_diagnostic.py" -ForegroundColor Yellow
    exit 1
}
Write-Host "  ✓ Diagnostic: $($diagResult.summary.ok)/$($diagResult.summary.total) OK" -ForegroundColor Green

# Check port 8100
$portInUse = Get-NetTCPConnection -LocalPort 8100 -ErrorAction SilentlyContinue
if ($portInUse) {
    Write-Host "  ⚠ Port 8100 in use, killing existing process..." -ForegroundColor Yellow
    $portInUse | ForEach-Object {
        Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
}
Write-Host "  ✓ Port 8100 available" -ForegroundColor Green

# ───────────────────────────────────────────────────────────────────────────
# SET ENVIRONMENT
# ───────────────────────────────────────────────────────────────────────────

Write-Host "`n[2/5] Setting environment..." -ForegroundColor Yellow

$env:AI_GATEWAY_MODE = $Mode
$env:AI_GATEWAY_PORT = "8100"

# Load secrets if exists
$secretsPath = "C:\secrets\hope.env"
if (Test-Path $secretsPath) {
    Get-Content $secretsPath | ForEach-Object {
        if ($_ -match "^([^=]+)=(.*)$") {
            [Environment]::SetEnvironmentVariable($matches[1], $matches[2])
        }
    }
    Write-Host "  ✓ Secrets loaded from $secretsPath" -ForegroundColor Green
} else {
    Write-Host "  ⚠ No secrets file found (Telegram/AI features disabled)" -ForegroundColor Yellow
}

Write-Host "  AI_GATEWAY_MODE = $Mode" -ForegroundColor Gray

# ───────────────────────────────────────────────────────────────────────────
# CREATE REQUIRED DIRECTORIES
# ───────────────────────────────────────────────────────────────────────────

Write-Host "`n[3/5] Creating directories..." -ForegroundColor Yellow

$dirs = @(
    "state\ai",
    "state\ai\outcomes",
    "state\events",
    "logs"
)

foreach ($dir in $dirs) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
        Write-Host "  Created: $dir" -ForegroundColor Gray
    }
}
Write-Host "  ✓ Directories ready" -ForegroundColor Green

# ───────────────────────────────────────────────────────────────────────────
# LAUNCH COMPONENTS
# ───────────────────────────────────────────────────────────────────────────

Write-Host "`n[4/5] Launching components..." -ForegroundColor Yellow

# Store PIDs for cleanup
$script:pids = @()

# Function to start component in new window
function Start-HopeComponent {
    param(
        [string]$Name,
        [string]$Command,
        [string]$Color = "White"
    )
    
    $proc = Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$WorkDir'; Write-Host '[$Name]' -ForegroundColor $Color; $Command" -PassThru
    $script:pids += $proc.Id
    Write-Host "  ✓ Started: $Name (PID: $($proc.Id))" -ForegroundColor Green
    Start-Sleep -Seconds 2
}

# 1. AI Gateway Server
Start-HopeComponent -Name "AI-GATEWAY" -Command "python -m ai_gateway.server" -Color "Cyan"

# 2. MoonBot Live Integration (watch mode)
Start-HopeComponent -Name "MOONBOT-LIVE" -Command "python -m ai_gateway.integrations.moonbot_live --watch" -Color "Yellow"

# 3. Price Feed Bridge (if not in DRY mode)
if ($Mode -ne "DRY") {
    Start-HopeComponent -Name "PRICE-FEED" -Command "python -m ai_gateway.feeds.price_feed_bridge" -Color "Magenta"
}

# ───────────────────────────────────────────────────────────────────────────
# MONITOR
# ───────────────────────────────────────────────────────────────────────────

Write-Host "`n[5/5] System status..." -ForegroundColor Yellow

# Wait for server to start
Start-Sleep -Seconds 3

# Health check
try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8100/health" -TimeoutSec 5
    Write-Host "  ✓ AI Gateway responding: $($health.status)" -ForegroundColor Green
} catch {
    Write-Host "  ⚠ AI Gateway not responding yet (may need more time)" -ForegroundColor Yellow
}

# ───────────────────────────────────────────────────────────────────────────
# SUMMARY
# ───────────────────────────────────────────────────────────────────────────

Write-Host "`n═══════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  HOPE AI SYSTEM LAUNCHED" -ForegroundColor Green
Write-Host "═══════════════════════════════════════════════════════════════" -ForegroundColor Cyan

Write-Host "`n  Mode: $Mode" -ForegroundColor $(if($Mode -eq "LIVE"){"Red"}elseif($Mode -eq "TESTNET"){"Yellow"}else{"Green"})
Write-Host "  Components: $($script:pids.Count) running"
Write-Host "  PIDs: $($script:pids -join ', ')"

Write-Host "`n  ENDPOINTS:" -ForegroundColor Yellow
Write-Host "    Health:    http://127.0.0.1:8100/health"
Write-Host "    Predict:   http://127.0.0.1:8100/predict/{symbol}"
Write-Host "    Stats:     http://127.0.0.1:8100/stats"

Write-Host "`n  MONITOR:" -ForegroundColor Yellow
Write-Host "    Decisions: Get-Content state\ai\decisions.jsonl -Wait -Tail 5"
Write-Host "    Outcomes:  Get-Content state\ai\outcomes\completed_outcomes.jsonl -Wait -Tail 5"

Write-Host "`n  STOP ALL:" -ForegroundColor Yellow
Write-Host "    .\stop_hope_ai.ps1"
Write-Host "    # or manually: Stop-Process -Id $($script:pids -join ',')"

if (-not $NoMonitor) {
    Write-Host "`n  Starting monitor in 3 seconds..." -ForegroundColor Gray
    Start-Sleep -Seconds 3
    
    # Open monitor window
    Start-Process powershell -ArgumentList "-NoExit", "-Command", @"
cd '$WorkDir'
Write-Host '═══════════════════════════════════════════════════════════════' -ForegroundColor Cyan
Write-Host '  HOPE AI MONITOR' -ForegroundColor Cyan
Write-Host '═══════════════════════════════════════════════════════════════' -ForegroundColor Cyan
Write-Host ''

`$lastDecision = ''
`$lastOutcome = ''

while (`$true) {
    Clear-Host
    Write-Host '═══════════════════════════════════════════════════════════════' -ForegroundColor Cyan
    Write-Host '  HOPE AI MONITOR - ' + (Get-Date -Format 'HH:mm:ss') -ForegroundColor Cyan
    Write-Host '═══════════════════════════════════════════════════════════════' -ForegroundColor Cyan
    
    # Health
    try {
        `$h = Invoke-RestMethod -Uri 'http://127.0.0.1:8100/health' -TimeoutSec 2
        Write-Host "`n  [HEALTH] `$(`$h.status)" -ForegroundColor Green
    } catch {
        Write-Host "`n  [HEALTH] OFFLINE" -ForegroundColor Red
    }
    
    # Recent decisions
    Write-Host "`n  [RECENT DECISIONS]" -ForegroundColor Yellow
    if (Test-Path 'state\ai\decisions.jsonl') {
        Get-Content 'state\ai\decisions.jsonl' -Tail 5 | ForEach-Object {
            `$d = `$_ | ConvertFrom-Json
            `$color = if(`$d.final_action -eq 'BUY'){'Green'}else{'Gray'}
            Write-Host "    `$(`$d.symbol) -> `$(`$d.final_action)" -ForegroundColor `$color
        }
    } else {
        Write-Host "    (no decisions yet)" -ForegroundColor Gray
    }
    
    # Stats
    Write-Host "`n  [STATS]" -ForegroundColor Yellow
    if (Test-Path 'state\ai\outcomes\completed_outcomes.jsonl') {
        `$outcomes = Get-Content 'state\ai\outcomes\completed_outcomes.jsonl' | ForEach-Object { `$_ | ConvertFrom-Json }
        `$total = `$outcomes.Count
        `$wins = (`$outcomes | Where-Object { `$_.pnl_pct -gt 0 }).Count
        `$rate = if(`$total -gt 0){[math]::Round(`$wins/`$total*100,1)}else{0}
        Write-Host "    Trades: `$total | Win Rate: `$rate%"
    } else {
        Write-Host "    (no outcomes yet)" -ForegroundColor Gray
    }
    
    Start-Sleep -Seconds 5
}
"@
}

Write-Host "`n  Press Ctrl+C to exit this window (components will keep running)" -ForegroundColor Gray
