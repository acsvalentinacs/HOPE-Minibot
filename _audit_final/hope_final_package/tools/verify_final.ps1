# ══════════════════════════════════════════════════════════════════════════════
# HOPE v4.0 FINAL — COMPREHENSIVE VERIFICATION
# ══════════════════════════════════════════════════════════════════════════════
# Запуск: powershell -ExecutionPolicy Bypass -File verify_final.ps1
# ══════════════════════════════════════════════════════════════════════════════

$ErrorActionPreference = "Continue"
$script:PassCount = 0
$script:FailCount = 0
$script:WarnCount = 0

function Test-Check {
    param(
        [string]$Name, 
        [scriptblock]$Test,
        [switch]$Critical
    )
    
    try {
        $result = & $Test
        if ($result) {
            Write-Host "  [OK] $Name" -ForegroundColor Green
            $script:PassCount++
            return $true
        } else {
            if ($Critical) {
                Write-Host "  [FAIL] $Name" -ForegroundColor Red
                $script:FailCount++
            } else {
                Write-Host "  [WARN] $Name" -ForegroundColor Yellow
                $script:WarnCount++
            }
            return $false
        }
    } catch {
        Write-Host "  [FAIL] $Name - $($_.Exception.Message)" -ForegroundColor Red
        $script:FailCount++
        return $false
    }
}

Write-Host "═══════════════════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "           HOPE v4.0 FINAL — COMPREHENSIVE VERIFICATION" -ForegroundColor Cyan
Write-Host "           $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1: CORE FILES
# ══════════════════════════════════════════════════════════════════════════════

Write-Host "[SECTION 1] Core Files" -ForegroundColor Yellow

$coreFiles = @(
    @{Path="core\pretrade_pipeline.py"; Critical=$true},
    @{Path="core\signal_gate.py"; Critical=$true},
    @{Path="core\adaptive_tp_engine.py"; Critical=$true},
    @{Path="core\trading_engine.py"; Critical=$true},
    @{Path="execution\binance_oco_executor.py"; Critical=$true},
    @{Path="execution\binance_live_client.py"; Critical=$false},
    @{Path="learning\trade_outcome_logger.py"; Critical=$false},
    @{Path="config\live_trade_policy.py"; Critical=$true},
    @{Path="config\signal_filter_rules.json"; Critical=$false},
    @{Path="scripts\pump_detector.py"; Critical=$true}
)

foreach ($file in $coreFiles) {
    Test-Check "File: $($file.Path)" { Test-Path $file.Path } -Critical:$file.Critical
}

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2: PYTHON SYNTAX
# ══════════════════════════════════════════════════════════════════════════════

Write-Host ""
Write-Host "[SECTION 2] Python Syntax" -ForegroundColor Yellow

$pythonFiles = Get-ChildItem -Path "core", "execution", "config", "learning" -Filter "*.py" -Recurse -ErrorAction SilentlyContinue

foreach ($file in $pythonFiles) {
    $relativePath = $file.FullName.Replace((Get-Location).Path + "\", "")
    Test-Check "Syntax: $relativePath" {
        $output = python -m py_compile $file.FullName 2>&1
        $LASTEXITCODE -eq 0
    } -Critical
}

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3: ENVIRONMENT
# ══════════════════════════════════════════════════════════════════════════════

Write-Host ""
Write-Host "[SECTION 3] Environment" -ForegroundColor Yellow

$envFile = "C:\secrets\hope.env"

Test-Check "Env file exists" { Test-Path $envFile } -Critical

if (Test-Path $envFile) {
    $envContent = Get-Content $envFile -Raw -ErrorAction SilentlyContinue
    
    Test-Check "BINANCE_API_KEY present" { $envContent -match "BINANCE_API_KEY" } -Critical
    Test-Check "BINANCE_API_SECRET present" { $envContent -match "BINANCE_API_SECRET" } -Critical
    Test-Check "HOPE_MODE defined" { $envContent -match "HOPE_MODE" }
    Test-Check "HOPE_LIVE_ACK defined" { $envContent -match "HOPE_LIVE_ACK" }
}

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4: PRETRADE PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

Write-Host ""
Write-Host "[SECTION 4] Pretrade Pipeline" -ForegroundColor Yellow

Test-Check "Pipeline import" {
    $code = "from core.pretrade_pipeline import pretrade_check; print('OK')"
    $output = python -c $code 2>&1
    $output -match "OK"
} -Critical

Test-Check "Pipeline rejects BTC" {
    $code = "from core.pretrade_pipeline import pretrade_check; r=pretrade_check({'symbol':'BTCUSDT','delta_pct':15.0,'type':'PUMP','daily_volume_m':1000,'price':84000}); print('BLOCKED' if not r.ok else 'PASSED')"
    $output = python -c $code 2>&1
    $output -match "BLOCKED"
} -Critical

Test-Check "Pipeline passes PEPE" {
    $code = "from core.pretrade_pipeline import pretrade_check; r=pretrade_check({'symbol':'PEPEUSDT','delta_pct':15.0,'type':'EXPLOSION','daily_volume_m':50,'price':0.00001}); print('PASSED' if r.ok else 'BLOCKED')"
    $output = python -c $code 2>&1
    $output -match "PASSED"
} -Critical

Test-Check "Pipeline rejects low liquidity" {
    $code = "from core.pretrade_pipeline import pretrade_check; r=pretrade_check({'symbol':'XYZUSDT','delta_pct':15.0,'type':'PUMP','daily_volume_m':0.1,'price':1.0}); print('BLOCKED' if not r.ok else 'PASSED')"
    $output = python -c $code 2>&1
    $output -match "BLOCKED"
} -Critical

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5: CIRCUIT BREAKER
# ══════════════════════════════════════════════════════════════════════════════

Write-Host ""
Write-Host "[SECTION 5] Circuit Breaker" -ForegroundColor Yellow

Test-Check "Circuit Breaker trips after 5 losses" {
    $code = @"
from core.pretrade_pipeline import CircuitBreaker, PipelineConfig
config = PipelineConfig()
cb = CircuitBreaker(config)
for i in range(5):
    cb.record_trade(-1.0)
print('TRIPPED' if cb.is_open() else 'OPEN')
"@
    $output = python -c $code 2>&1
    $output -match "TRIPPED"
} -Critical

Test-Check "Circuit Breaker 5% daily limit" {
    $code = "from core.pretrade_pipeline import PipelineConfig; c=PipelineConfig(); print('OK' if c.max_daily_loss_pct == 5.0 else 'WRONG')"
    $output = python -c $code 2>&1
    $output -match "OK"
} -Critical

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6: ADAPTIVE TP
# ══════════════════════════════════════════════════════════════════════════════

Write-Host ""
Write-Host "[SECTION 6] Adaptive TP" -ForegroundColor Yellow

Test-Check "Adaptive TP import" {
    $code = "from core.adaptive_tp_engine import calculate_adaptive_tp; print('OK')"
    $output = python -c $code 2>&1
    $output -match "OK"
} -Critical

Test-Check "R:R >= 2.5 for delta 15%" {
    $code = "from core.adaptive_tp_engine import calculate_adaptive_tp; r=calculate_adaptive_tp(15.0,0.75); print('OK' if r.effective_rr >= 2.5 else 'FAIL')"
    $output = python -c $code 2>&1
    $output -match "OK"
} -Critical

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7: LIVE BARRIER
# ══════════════════════════════════════════════════════════════════════════════

Write-Host ""
Write-Host "[SECTION 7] Live Barrier" -ForegroundColor Yellow

Test-Check "Live Barrier DRY by default" {
    $code = "from core.pretrade_pipeline import LiveBarrier, PipelineConfig, ExecutionMode; b=LiveBarrier(PipelineConfig()); print('DRY' if b.effective_mode == ExecutionMode.DRY else b.effective_mode.value)"
    $output = python -c $code 2>&1
    $output -match "DRY"
} -Critical

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 8: PUMP DETECTOR INTEGRATION
# ══════════════════════════════════════════════════════════════════════════════

Write-Host ""
Write-Host "[SECTION 8] Pump Detector Integration" -ForegroundColor Yellow

Test-Check "pump_detector.py exists" { Test-Path "scripts\pump_detector.py" } -Critical

$pdContent = Get-Content "scripts\pump_detector.py" -Raw -ErrorAction SilentlyContinue

Test-Check "HARD FILTER present" { $pdContent -match "HARD.*FILTER|HOPE.*v4|pretrade_check|TRADING_ENGINE" }

Test-Check "No duplicate processes" {
    $procs = Get-Process python -ErrorAction SilentlyContinue | Where-Object {
        $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)" -ErrorAction SilentlyContinue).CommandLine
        $cmd -match "pump_detector"
    }
    $procs.Count -le 1
}

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 9: DIRECTORIES
# ══════════════════════════════════════════════════════════════════════════════

Write-Host ""
Write-Host "[SECTION 9] Directories" -ForegroundColor Yellow

$requiredDirs = @("state", "state\trades", "state\ai", "logs", "data")

foreach ($dir in $requiredDirs) {
    Test-Check "Directory: $dir" { Test-Path $dir -PathType Container }
}

# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════════

Write-Host ""
Write-Host "═══════════════════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "           VERIFICATION SUMMARY" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  PASS: $script:PassCount" -ForegroundColor Green
Write-Host "  WARN: $script:WarnCount" -ForegroundColor Yellow
Write-Host "  FAIL: $script:FailCount" -ForegroundColor $(if ($script:FailCount -gt 0) { "Red" } else { "Green" })

$totalCritical = $script:PassCount + $script:FailCount

if ($script:FailCount -eq 0) {
    Write-Host ""
    Write-Host "╔═══════════════════════════════════════════════════════════════════════════════╗" -ForegroundColor Green
    Write-Host "║                         ✅ ALL CRITICAL TESTS PASSED                          ║" -ForegroundColor Green
    Write-Host "║                         SYSTEM READY FOR TRADING                              ║" -ForegroundColor Green
    Write-Host "╚═══════════════════════════════════════════════════════════════════════════════╝" -ForegroundColor Green
    
    Write-Host ""
    Write-Host "Next steps:" -ForegroundColor Cyan
    Write-Host "  1. DRY RUN: python scripts\pump_detector.py --top 20" -ForegroundColor White
    Write-Host "  2. Monitor Telegram for 30 minutes" -ForegroundColor White
    Write-Host "  3. Check state\trades\trades.jsonl for logged signals" -ForegroundColor White
    Write-Host ""
    
    exit 0
} else {
    Write-Host ""
    Write-Host "╔═══════════════════════════════════════════════════════════════════════════════╗" -ForegroundColor Red
    Write-Host "║                         ❌ SOME CRITICAL TESTS FAILED                         ║" -ForegroundColor Red
    Write-Host "║                         FIX ISSUES BEFORE TRADING                             ║" -ForegroundColor Red
    Write-Host "╚═══════════════════════════════════════════════════════════════════════════════╝" -ForegroundColor Red
    exit 1
}
