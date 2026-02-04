# ══════════════════════════════════════════════════════════════════════════════
# HOPE LIVE READY VERIFICATION
# ══════════════════════════════════════════════════════════════════════════════
# Запуск: powershell -ExecutionPolicy Bypass -File verify_live_ready.ps1
# ══════════════════════════════════════════════════════════════════════════════

$ErrorActionPreference = "Continue"
$script:PassCount = 0
$script:FailCount = 0

function Test-Check {
    param([string]$Name, [scriptblock]$Test)
    
    try {
        $result = & $Test
        if ($result) {
            Write-Host "  [OK] $Name" -ForegroundColor Green
            $script:PassCount++
            return $true
        } else {
            Write-Host "  [FAIL] $Name" -ForegroundColor Red
            $script:FailCount++
            return $false
        }
    } catch {
        Write-Host "  [FAIL] $Name - $($_.Exception.Message)" -ForegroundColor Red
        $script:FailCount++
        return $false
    }
}

Write-Host "═══════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "           HOPE v4.0 LIVE READINESS VERIFICATION" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1: FILES
# ══════════════════════════════════════════════════════════════════════════════

Write-Host "[SECTION 1] Core Files" -ForegroundColor Yellow

$coreFiles = @(
    "core\signal_gate.py",
    "core\adaptive_tp_engine.py",
    "core\trading_engine.py",
    "core\live_trading_patch.py",
    "execution\binance_oco_executor.py",
    "execution\binance_live_client.py",
    "learning\trade_outcome_logger.py",
    "config\live_trade_policy.py",
    "scripts\pump_detector.py"
)

foreach ($file in $coreFiles) {
    Test-Check "File: $file" { Test-Path $file }
}

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2: PYTHON SYNTAX
# ══════════════════════════════════════════════════════════════════════════════

Write-Host ""
Write-Host "[SECTION 2] Python Syntax" -ForegroundColor Yellow

$pythonFiles = @(
    "core\signal_gate.py",
    "core\adaptive_tp_engine.py",
    "core\trading_engine.py",
    "execution\binance_oco_executor.py",
    "config\live_trade_policy.py"
)

foreach ($file in $pythonFiles) {
    if (Test-Path $file) {
        Test-Check "Syntax: $file" {
            $output = python -m py_compile $file 2>&1
            $LASTEXITCODE -eq 0
        }
    }
}

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3: ENVIRONMENT
# ══════════════════════════════════════════════════════════════════════════════

Write-Host ""
Write-Host "[SECTION 3] Environment" -ForegroundColor Yellow

$envFile = "C:\secrets\hope.env"

Test-Check "Env file exists" { Test-Path $envFile }

if (Test-Path $envFile) {
    $envContent = Get-Content $envFile -Raw
    
    Test-Check "BINANCE_API_KEY present" { $envContent -match "BINANCE_API_KEY" }
    Test-Check "BINANCE_API_SECRET present" { $envContent -match "BINANCE_API_SECRET" }
    Test-Check "HOPE_MODE defined" { $envContent -match "HOPE_MODE" }
}

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4: SIGNAL GATE TEST
# ══════════════════════════════════════════════════════════════════════════════

Write-Host ""
Write-Host "[SECTION 4] Signal Gate" -ForegroundColor Yellow

Test-Check "Signal Gate import" {
    $code = "from core.signal_gate import SignalGate; print('OK')"
    $output = python -c $code 2>&1
    $output -match "OK"
}

Test-Check "Signal Gate blocks BTC" {
    $code = "from core.signal_gate import SignalGate, GateDecision; g=SignalGate(); d,r,_=g.check({'symbol':'BTCUSDT','delta_pct':15.0,'type':'PUMP'}); print('BLOCKED' if d in (GateDecision.BLOCK, GateDecision.PASS_LOG_ONLY) else 'PASSED')"
    $output = python -c $code 2>&1
    $output -match "BLOCKED"
}

Test-Check "Signal Gate passes PEPE" {
    $code = "from core.signal_gate import SignalGate, GateDecision; g=SignalGate(); d,r,_=g.check({'symbol':'PEPEUSDT','delta_pct':15.0,'type':'EXPLOSION'}); print('PASSED' if d in (GateDecision.PASS_TELEGRAM_AND_TRADE, GateDecision.PASS_TRADE_ONLY) else 'BLOCKED')"
    $output = python -c $code 2>&1
    $output -match "PASSED"
}

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5: ADAPTIVE TP TEST
# ══════════════════════════════════════════════════════════════════════════════

Write-Host ""
Write-Host "[SECTION 5] Adaptive TP" -ForegroundColor Yellow

Test-Check "Adaptive TP import" {
    $code = "from core.adaptive_tp_engine import calculate_adaptive_tp; print('OK')"
    $output = python -c $code 2>&1
    $output -match "OK"
}

Test-Check "R:R >= 2.5 for delta 15%" {
    $code = "from core.adaptive_tp_engine import calculate_adaptive_tp; r=calculate_adaptive_tp(15.0,0.75); print('OK' if r.effective_rr >= 2.5 else 'FAIL')"
    $output = python -c $code 2>&1
    $output -match "OK"
}

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6: LIVE POLICY TEST
# ══════════════════════════════════════════════════════════════════════════════

Write-Host ""
Write-Host "[SECTION 6] Live Policy" -ForegroundColor Yellow

Test-Check "Live Policy import" {
    $code = "from config.live_trade_policy import check_symbol_allowed; print('OK')"
    $output = python -c $code 2>&1
    $output -match "OK"
}

Test-Check "BTC blocked by policy" {
    $code = "from config.live_trade_policy import check_symbol_allowed; ok,r=check_symbol_allowed('BTCUSDT'); print('BLOCKED' if not ok else 'PASSED')"
    $output = python -c $code 2>&1
    $output -match "BLOCKED"
}

# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════════

Write-Host ""
Write-Host "═══════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "           VERIFICATION SUMMARY" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  PASS: $script:PassCount" -ForegroundColor Green
Write-Host "  FAIL: $script:FailCount" -ForegroundColor $(if ($script:FailCount -gt 0) { "Red" } else { "Green" })

if ($script:FailCount -eq 0) {
    Write-Host ""
    Write-Host "╔═══════════════════════════════════════════════════════════════════╗" -ForegroundColor Green
    Write-Host "║                    ✅ ALL TESTS PASSED                            ║" -ForegroundColor Green
    Write-Host "║                    SYSTEM READY FOR LIVE                          ║" -ForegroundColor Green
    Write-Host "╚═══════════════════════════════════════════════════════════════════╝" -ForegroundColor Green
    exit 0
} else {
    Write-Host ""
    Write-Host "╔═══════════════════════════════════════════════════════════════════╗" -ForegroundColor Red
    Write-Host "║                    ❌ SOME TESTS FAILED                           ║" -ForegroundColor Red
    Write-Host "║                    FIX ISSUES BEFORE LIVE                         ║" -ForegroundColor Red
    Write-Host "╚═══════════════════════════════════════════════════════════════════╝" -ForegroundColor Red
    exit 1
}
