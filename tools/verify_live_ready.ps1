# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-30 22:55:00 UTC
# Purpose: Verify LIVE trading readiness
# === END SIGNATURE ===
<#
.SYNOPSIS
    HOPE LIVE READY VERIFICATION v1.0

.DESCRIPTION
    Checks all components required for LIVE trading.
    Does NOT execute any real trades.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\verify_live_ready.ps1
#>

$ErrorActionPreference = "Continue"
$script:PassCount = 0
$script:FailCount = 0

function Write-Check {
    param([string]$Name, [bool]$Passed, [string]$Detail = "")
    if ($Passed) {
        Write-Host "  [OK]   $Name" -ForegroundColor Green
        $script:PassCount++
    } else {
        Write-Host "  [FAIL] $Name" -ForegroundColor Red
        if ($Detail) { Write-Host "         $Detail" -ForegroundColor Yellow }
        $script:FailCount++
    }
}

Write-Host "=" * 70
Write-Host "HOPE LIVE READY VERIFICATION"
Write-Host "=" * 70
Write-Host ""

# Change to project directory
$ProjectRoot = "C:\Users\kirillDev\Desktop\TradingBot\minibot"
Set-Location $ProjectRoot

Write-Host "Project: $ProjectRoot"
Write-Host "Time: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host ""

# ─────────────────────────────────────────────────────────────────────────────
# 1. CORE MODULES
# ─────────────────────────────────────────────────────────────────────────────
Write-Host "─" * 70
Write-Host "1. CORE MODULES"
Write-Host "─" * 70

$modules = @(
    "core\signal_gate.py",
    "core\adaptive_tp_engine.py",
    "core\trading_engine.py",
    "execution\binance_oco_executor.py",
    "execution\binance_live_client.py",
    "scripts\live_trader_v4.py",
    "scripts\eye_of_god_adapter.py",
    "config\live_trade_policy.py"
)

foreach ($mod in $modules) {
    $exists = Test-Path $mod
    Write-Check $mod $exists
}
Write-Host ""

# ─────────────────────────────────────────────────────────────────────────────
# 2. SYNTAX CHECK
# ─────────────────────────────────────────────────────────────────────────────
Write-Host "─" * 70
Write-Host "2. PYTHON SYNTAX"
Write-Host "─" * 70

$pyFiles = @(
    "core\signal_gate.py",
    "core\adaptive_tp_engine.py",
    "execution\binance_live_client.py",
    "scripts\live_trader_v4.py"
)

foreach ($pyf in $pyFiles) {
    if (Test-Path $pyf) {
        $result = python -m py_compile $pyf 2>&1
        $ok = $LASTEXITCODE -eq 0
        Write-Check "py_compile $pyf" $ok $result
    }
}
Write-Host ""

# ─────────────────────────────────────────────────────────────────────────────
# 3. IMPORTS
# ─────────────────────────────────────────────────────────────────────────────
Write-Host "─" * 70
Write-Host "3. IMPORT CHECKS"
Write-Host "─" * 70

# Signal Gate
$out = python -c "from core.signal_gate import SignalGate; print('ok')" 2>&1
Write-Check "core.signal_gate" ($out -match "ok")

# Adaptive TP
$out = python -c "from core.adaptive_tp_engine import calculate_adaptive_tp; print('ok')" 2>&1
Write-Check "core.adaptive_tp_engine" ($out -match "ok")

# Live Client
$out = python -c "from execution.binance_live_client import check_live_barrier; print('ok')" 2>&1
Write-Check "execution.binance_live_client" ($out -match "ok")

# Eye Adapter
$out = python -c "from scripts.eye_of_god_adapter import EyeOfGodAdapter; print('ok')" 2>&1
Write-Check "scripts.eye_of_god_adapter" ($out -match "ok")

Write-Host ""

# ─────────────────────────────────────────────────────────────────────────────
# 4. LIVE BARRIER
# ─────────────────────────────────────────────────────────────────────────────
Write-Host "─" * 70
Write-Host "4. LIVE BARRIER STATUS"
Write-Host "─" * 70

$barrierCode = @"
import sys
sys.path.insert(0, '.')
from execution.binance_live_client import check_live_barrier
ready, msg = check_live_barrier()
print(msg)
print('BARRIER_READY=' + str(ready))
"@

$barrierOut = python -c $barrierCode 2>&1
Write-Host $barrierOut
Write-Host ""

# ─────────────────────────────────────────────────────────────────────────────
# 5. SIGNAL GATE TEST
# ─────────────────────────────────────────────────────────────────────────────
Write-Host "─" * 70
Write-Host "5. SIGNAL GATE TEST"
Write-Host "─" * 70

$gateCode = @"
import sys
sys.path.insert(0, '.')
from core.signal_gate import SignalGate, GateDecision
g = SignalGate()
tests = [
    {'symbol': 'PEPEUSDT', 'delta_pct': 15.0, 'type': 'EXPLOSION'},
    {'symbol': 'BTCUSDT', 'delta_pct': 5.0, 'type': 'PUMP'},
    {'symbol': 'ADAUSDT', 'delta_pct': 0.5, 'type': 'MICRO'},
]
for t in tests:
    d, r, det = g.check(t)
    status = 'PASS' if d in (GateDecision.PASS_TELEGRAM_AND_TRADE, GateDecision.PASS_TRADE_ONLY) else 'LOG' if d == GateDecision.PASS_LOG_ONLY else 'BLOCK'
    print(f"{t['symbol']:12} delta={t['delta_pct']:5.1f}% -> {status}")
print('GATE_OK')
"@

$gateOut = python -c $gateCode 2>&1
Write-Host $gateOut
$gateOk = $gateOut -match "GATE_OK"
Write-Check "Signal Gate functional" $gateOk
Write-Host ""

# ─────────────────────────────────────────────────────────────────────────────
# 6. EYE OF GOD ADAPTER TEST
# ─────────────────────────────────────────────────────────────────────────────
Write-Host "─" * 70
Write-Host "6. EYE OF GOD ADAPTER TEST"
Write-Host "─" * 70

$eyeCode = @"
import sys
sys.path.insert(0, '.')
from scripts.eye_of_god_adapter import EyeOfGodAdapter
a = EyeOfGodAdapter()
print(f'patch={a.patch_detail}')
result = a.analyze({'symbol': 'PEPEUSDT', 'delta_pct': 15.0, 'type': 'EXPLOSION'})
print(f"action={result.get('action', 'N/A')}")
print('EYE_OK')
"@

$eyeOut = python -c $eyeCode 2>&1
Write-Host $eyeOut
$eyeOk = $eyeOut -match "EYE_OK"
Write-Check "Eye of God Adapter functional" $eyeOk
Write-Host ""

# ─────────────────────────────────────────────────────────────────────────────
# 7. DRY RUN TEST
# ─────────────────────────────────────────────────────────────────────────────
Write-Host "─" * 70
Write-Host "7. DRY RUN TEST"
Write-Host "─" * 70

# Create test signal file
$testSignal = '{"signal_id":"test:1","symbol":"PEPEUSDT","delta_pct":15.0,"type":"EXPLOSION","confidence":0.8}'
$testFile = "state\test_signal_verify.jsonl"
$testSignal | Out-File -FilePath $testFile -Encoding utf8

$dryOut = python scripts\live_trader_v4.py --dry --signals $testFile --max-signals 1 2>&1
$dryOk = $dryOut -match "DONE"
Write-Host ($dryOut | Select-Object -Last 10 | Out-String)
Write-Check "DRY run completed" $dryOk

# Cleanup
Remove-Item $testFile -ErrorAction SilentlyContinue
Write-Host ""

# ─────────────────────────────────────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
Write-Host "=" * 70
Write-Host "VERIFICATION SUMMARY"
Write-Host "=" * 70
Write-Host "  Passed: $script:PassCount"
Write-Host "  Failed: $script:FailCount"
Write-Host ""

if ($script:FailCount -eq 0) {
    Write-Host "[PASS] System is LIVE-ready (set env vars to enable)" -ForegroundColor Green
    exit 0
} else {
    Write-Host "[FAIL] Fix issues above before LIVE trading" -ForegroundColor Red
    exit 1
}
