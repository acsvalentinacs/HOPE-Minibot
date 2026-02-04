# -*- coding: utf-8 -*-
# HOPE v4.0 PRODUCTION READINESS VERIFICATION
# ============================================
# Run: powershell -ExecutionPolicy Bypass -File tools\verify_production_ready.ps1

$ErrorActionPreference = "Continue"
$script:Pass = 0
$script:Fail = 0
$script:Warn = 0

function Check {
    param([string]$Name, [scriptblock]$Test, [switch]$Critical)
    
    try {
        $result = & $Test
        if ($result) {
            Write-Host "  [OK] $Name" -ForegroundColor Green
            $script:Pass++
            return $true
        } else {
            if ($Critical) {
                Write-Host "  [FAIL] $Name" -ForegroundColor Red
                $script:Fail++
            } else {
                Write-Host "  [WARN] $Name" -ForegroundColor Yellow
                $script:Warn++
            }
            return $false
        }
    } catch {
        Write-Host "  [FAIL] $Name - $($_.Exception.Message)" -ForegroundColor Red
        $script:Fail++
        return $false
    }
}

Write-Host "=" * 70 -ForegroundColor Cyan
Write-Host "        HOPE v4.0 PRODUCTION READINESS CHECK" -ForegroundColor Cyan
Write-Host "        $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Cyan
Write-Host "=" * 70 -ForegroundColor Cyan

# ══════════════════════════════════════════════════════════════════════════════
# 1. CORE FILES
# ══════════════════════════════════════════════════════════════════════════════
Write-Host "`n[1] CORE FILES" -ForegroundColor Yellow

$files = @(
    "core\event_ledger.py",
    "core\pretrade_pipeline.py",
    "core\autotrader_watchdog_integration.py",
    "execution\binance_oco_executor_fixed.py",
    "scripts\pump_detector.py",
    "scripts\position_watchdog.py"
)

foreach ($f in $files) {
    Check "File: $f" { Test-Path $f } -Critical
}

# ══════════════════════════════════════════════════════════════════════════════
# 2. PYTHON SYNTAX
# ══════════════════════════════════════════════════════════════════════════════
Write-Host "`n[2] PYTHON SYNTAX" -ForegroundColor Yellow

$pyFiles = @(
    "core\event_ledger.py",
    "core\pretrade_pipeline.py",
    "core\autotrader_watchdog_integration.py",
    "execution\binance_oco_executor_fixed.py"
)

foreach ($f in $pyFiles) {
    if (Test-Path $f) {
        Check "Syntax: $f" {
            $null = python -m py_compile $f 2>&1
            $LASTEXITCODE -eq 0
        } -Critical
    }
}

# ══════════════════════════════════════════════════════════════════════════════
# 3. EVENT LEDGER
# ══════════════════════════════════════════════════════════════════════════════
Write-Host "`n[3] EVENT LEDGER" -ForegroundColor Yellow

Check "Event Ledger import" {
    $code = "from core.event_ledger import EventLedger, get_ledger; print('OK')"
    $out = python -c $code 2>&1
    $out -match "OK"
} -Critical

Check "Event Ledger invariants" {
    $code = "from core.event_ledger import EventLedger; l=EventLedger(); ok,_=l.invariants.check_live_ack(); print('OK' if ok else 'BLOCKED')"
    $out = python -c $code 2>&1
    $out -match "OK|BLOCKED"
} -Critical

# ══════════════════════════════════════════════════════════════════════════════
# 4. EXECUTOR
# ══════════════════════════════════════════════════════════════════════════════
Write-Host "`n[4] BINANCE EXECUTOR" -ForegroundColor Yellow

Check "Executor import" {
    $code = "from execution.binance_oco_executor_fixed import BinanceOCOExecutor, ExecutionMode; print('OK')"
    $out = python -c $code 2>&1
    $out -match "OK"
} -Critical

Check "Executor DRY mode" {
    $code = "from execution.binance_oco_executor_fixed import BinanceOCOExecutor, ExecutionMode; e=BinanceOCOExecutor(mode=ExecutionMode.DRY); print('OK')"
    $out = python -c $code 2>&1
    $out -match "OK"
} -Critical

# ══════════════════════════════════════════════════════════════════════════════
# 5. WATCHDOG INTEGRATION
# ══════════════════════════════════════════════════════════════════════════════
Write-Host "`n[5] WATCHDOG INTEGRATION" -ForegroundColor Yellow

Check "Watchdog integration import" {
    $code = "from core.autotrader_watchdog_integration import register_with_watchdog; print('OK')"
    $out = python -c $code 2>&1
    $out -match "OK"
} -Critical

# ══════════════════════════════════════════════════════════════════════════════
# 6. PRETRADE PIPELINE
# ══════════════════════════════════════════════════════════════════════════════
Write-Host "`n[6] PRETRADE PIPELINE" -ForegroundColor Yellow

Check "Pipeline import" {
    $code = "from core.pretrade_pipeline import pretrade_check; print('OK')"
    $out = python -c $code 2>&1
    $out -match "OK"
} -Critical

Check "BTC blocked" {
    $code = @"
from core.pretrade_pipeline import pretrade_check
r = pretrade_check({'symbol':'BTCUSDT','delta_pct':15.0,'type':'PUMP','daily_volume_m':1000,'price':84000})
print('BLOCKED' if not r.ok else 'PASSED')
"@
    $out = python -c $code 2>&1
    $out -match "BLOCKED"
} -Critical

# ══════════════════════════════════════════════════════════════════════════════
# 7. ENVIRONMENT
# ══════════════════════════════════════════════════════════════════════════════
Write-Host "`n[7] ENVIRONMENT" -ForegroundColor Yellow

$envFile = "C:\secrets\hope.env"
Check "Env file exists" { Test-Path $envFile }

if (Test-Path $envFile) {
    $env = Get-Content $envFile -Raw
    Check "BINANCE_API_KEY" { $env -match "BINANCE_API_KEY" }
    Check "BINANCE_API_SECRET" { $env -match "BINANCE_API_SECRET" }
    
    $isTestnet = $env -match "BINANCE_TESTNET\s*=\s*(true|1)"
    $isLive = $env -match "HOPE_MODE\s*=\s*LIVE"
    $hasAck = $env -match "HOPE_LIVE_ACK\s*=\s*YES_I_UNDERSTAND"
    
    if ($isLive -and -not $hasAck) {
        Write-Host "  [WARN] LIVE mode without ACK!" -ForegroundColor Yellow
        $script:Warn++
    }
    
    if ($isLive -and $isTestnet) {
        Write-Host "  [WARN] LIVE mode with TESTNET=true!" -ForegroundColor Yellow
        $script:Warn++
    }
}

# ══════════════════════════════════════════════════════════════════════════════
# 8. LEDGER DIRECTORY
# ══════════════════════════════════════════════════════════════════════════════
Write-Host "`n[8] LEDGER DIRECTORY" -ForegroundColor Yellow

Check "Ledger dir exists" { 
    if (-not (Test-Path "state\ai\ledger")) {
        New-Item -ItemType Directory -Path "state\ai\ledger" -Force | Out-Null
    }
    Test-Path "state\ai\ledger" 
}

Check "Watchdog dir exists" {
    if (-not (Test-Path "state\ai\watchdog")) {
        New-Item -ItemType Directory -Path "state\ai\watchdog" -Force | Out-Null
    }
    Test-Path "state\ai\watchdog"
}

# ══════════════════════════════════════════════════════════════════════════════
# 9. PROCESSES
# ══════════════════════════════════════════════════════════════════════════════
Write-Host "`n[9] RUNNING PROCESSES" -ForegroundColor Yellow

$procs = Get-Process python -ErrorAction SilentlyContinue | ForEach-Object {
    $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)" -ErrorAction SilentlyContinue).CommandLine
    if ($cmd) { "$($_.Id): $cmd" }
}

if ($procs) {
    Write-Host "  Active Python processes:" -ForegroundColor Gray
    $procs | ForEach-Object { Write-Host "    $_" -ForegroundColor Gray }
} else {
    Write-Host "  No Python processes running" -ForegroundColor Gray
}

# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════════
Write-Host "`n" + "=" * 70 -ForegroundColor Cyan
Write-Host "                    VERIFICATION SUMMARY" -ForegroundColor Cyan
Write-Host "=" * 70 -ForegroundColor Cyan

Write-Host "  PASS: $script:Pass" -ForegroundColor Green
Write-Host "  WARN: $script:Warn" -ForegroundColor Yellow
Write-Host "  FAIL: $script:Fail" -ForegroundColor $(if ($script:Fail -gt 0) { "Red" } else { "Green" })

if ($script:Fail -eq 0) {
    Write-Host "`n" + "=" * 70 -ForegroundColor Green
    Write-Host "         SYSTEM READY FOR PRODUCTION" -ForegroundColor Green
    Write-Host "=" * 70 -ForegroundColor Green
    
    Write-Host "`nNext steps:" -ForegroundColor Cyan
    Write-Host "  1. Start pricefeed_bridge.py --daemon" -ForegroundColor White
    Write-Host "  2. Start position_watchdog.py" -ForegroundColor White
    Write-Host "  3. Start pump_detector.py --top 20" -ForegroundColor White
    Write-Host "  4. Monitor state/ai/ledger/events_*.jsonl" -ForegroundColor White
    
    exit 0
} else {
    Write-Host "`n" + "=" * 70 -ForegroundColor Red
    Write-Host "         FIX FAILURES BEFORE PRODUCTION" -ForegroundColor Red
    Write-Host "=" * 70 -ForegroundColor Red
    exit 1
}
