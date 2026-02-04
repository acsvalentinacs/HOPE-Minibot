# ══════════════════════════════════════════════════════════════════════════════
# HOPE AI v4.0 - VERIFY SCRIPT
# Проверка что ВСЁ работает
# ══════════════════════════════════════════════════════════════════════════════
# Использование: .\verify.ps1
# ══════════════════════════════════════════════════════════════════════════════

$ErrorActionPreference = "Continue"
$VerbosePreference = "Continue"

$TARGET_DIR = "C:\Users\kirillDev\Desktop\TradingBot\minibot"
$PASS_COUNT = 0
$FAIL_COUNT = 0

Write-Host "═══════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "           HOPE AI v4.0 - VERIFY SCRIPT" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""

# ══════════════════════════════════════════════════════════════════════════════
# TEST 1: Check required files exist
# ══════════════════════════════════════════════════════════════════════════════

Write-Host "[TEST 1] Required files exist..." -ForegroundColor Yellow

$requiredFiles = @(
    "scripts\pump_detector.py",
    "core\signal_gate.py",
    "config\signal_filter_rules.json"
)

$filesOk = $true
foreach ($file in $requiredFiles) {
    $fullPath = Join-Path $TARGET_DIR $file
    if (Test-Path $fullPath) {
        Write-Host "  ✅ $file" -ForegroundColor Green
    } else {
        Write-Host "  ❌ $file NOT FOUND" -ForegroundColor Red
        $filesOk = $false
    }
}

if ($filesOk) {
    Write-Host "  [PASS] All required files exist" -ForegroundColor Green
    $PASS_COUNT++
} else {
    Write-Host "  [FAIL] Some files missing" -ForegroundColor Red
    $FAIL_COUNT++
}
Write-Host ""

# ══════════════════════════════════════════════════════════════════════════════
# TEST 2: Python syntax check
# ══════════════════════════════════════════════════════════════════════════════

Write-Host "[TEST 2] Python syntax check..." -ForegroundColor Yellow

$pyFiles = @(
    "scripts\pump_detector.py",
    "core\signal_gate.py"
)

$syntaxOk = $true
foreach ($file in $pyFiles) {
    $fullPath = Join-Path $TARGET_DIR $file
    if (Test-Path $fullPath) {
        $result = python -m py_compile $fullPath 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  ✅ $file" -ForegroundColor Green
        } else {
            Write-Host "  ❌ $file SYNTAX ERROR: $result" -ForegroundColor Red
            $syntaxOk = $false
        }
    }
}

if ($syntaxOk) {
    Write-Host "  [PASS] All syntax OK" -ForegroundColor Green
    $PASS_COUNT++
} else {
    Write-Host "  [FAIL] Syntax errors found" -ForegroundColor Red
    $FAIL_COUNT++
}
Write-Host ""

# ══════════════════════════════════════════════════════════════════════════════
# TEST 3: HARD FILTER is present in pump_detector.py
# ══════════════════════════════════════════════════════════════════════════════

Write-Host "[TEST 3] HARD FILTER present in pump_detector.py..." -ForegroundColor Yellow

$pumpDetectorPath = Join-Path $TARGET_DIR "scripts\pump_detector.py"
if (Test-Path $pumpDetectorPath) {
    $content = Get-Content $pumpDetectorPath -Raw
    
    if ($content -match "HARD TELEGRAM FILTER - BEGIN") {
        Write-Host "  ✅ HARD FILTER marker found" -ForegroundColor Green
        
        if ($content -match 'if _delta < 10\.0') {
            Write-Host "  ✅ Delta threshold 10.0% present" -ForegroundColor Green
        } else {
            Write-Host "  ❌ Delta threshold NOT FOUND" -ForegroundColor Red
            $FAIL_COUNT++
        }
        
        if ($content -match 'if _type in \("MICRO"') {
            Write-Host "  ✅ Type filter present" -ForegroundColor Green
        } else {
            Write-Host "  ❌ Type filter NOT FOUND" -ForegroundColor Red
            $FAIL_COUNT++
        }
        
        Write-Host "  [PASS] HARD FILTER integrated" -ForegroundColor Green
        $PASS_COUNT++
    } else {
        Write-Host "  ❌ HARD FILTER NOT FOUND" -ForegroundColor Red
        Write-Host "  Run install.ps1 first!" -ForegroundColor Yellow
        $FAIL_COUNT++
    }
} else {
    Write-Host "  ❌ pump_detector.py NOT FOUND" -ForegroundColor Red
    $FAIL_COUNT++
}
Write-Host ""

# ══════════════════════════════════════════════════════════════════════════════
# TEST 4: No duplicate processes
# ══════════════════════════════════════════════════════════════════════════════

Write-Host "[TEST 4] No duplicate processes..." -ForegroundColor Yellow

$processes = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | 
    Where-Object { $_.CommandLine -match "pump_detector" }

$procCount = ($processes | Measure-Object).Count

if ($procCount -eq 0) {
    Write-Host "  ✅ No pump_detector running (ready to start)" -ForegroundColor Green
    $PASS_COUNT++
} elseif ($procCount -eq 1) {
    Write-Host "  ✅ Exactly 1 pump_detector running" -ForegroundColor Green
    $PASS_COUNT++
} else {
    Write-Host "  ❌ $procCount pump_detector processes (should be 0 or 1)" -ForegroundColor Red
    Write-Host "  Run: Get-Process python | Stop-Process -Force" -ForegroundColor Yellow
    $FAIL_COUNT++
}
Write-Host ""

# ══════════════════════════════════════════════════════════════════════════════
# TEST 5: Config files valid JSON
# ══════════════════════════════════════════════════════════════════════════════

Write-Host "[TEST 5] Config files valid JSON..." -ForegroundColor Yellow

$jsonFiles = @(
    "config\signal_filter_rules.json"
)

$jsonOk = $true
foreach ($file in $jsonFiles) {
    $fullPath = Join-Path $TARGET_DIR $file
    if (Test-Path $fullPath) {
        try {
            $null = Get-Content $fullPath -Raw | ConvertFrom-Json
            Write-Host "  ✅ $file" -ForegroundColor Green
        } catch {
            Write-Host "  ❌ $file INVALID JSON: $_" -ForegroundColor Red
            $jsonOk = $false
        }
    } else {
        Write-Host "  ⚠️ $file not found (will use defaults)" -ForegroundColor Yellow
    }
}

if ($jsonOk) {
    Write-Host "  [PASS] Config files OK" -ForegroundColor Green
    $PASS_COUNT++
} else {
    Write-Host "  [FAIL] Config errors" -ForegroundColor Red
    $FAIL_COUNT++
}
Write-Host ""

# ══════════════════════════════════════════════════════════════════════════════
# TEST 6: Directories exist
# ══════════════════════════════════════════════════════════════════════════════

Write-Host "[TEST 6] Required directories exist..." -ForegroundColor Yellow

$requiredDirs = @(
    "core",
    "config",
    "scripts",
    "state",
    "logs"
)

$dirsOk = $true
foreach ($dir in $requiredDirs) {
    $fullPath = Join-Path $TARGET_DIR $dir
    if (Test-Path $fullPath) {
        Write-Host "  ✅ $dir" -ForegroundColor Green
    } else {
        Write-Host "  ❌ $dir NOT FOUND" -ForegroundColor Red
        $dirsOk = $false
    }
}

if ($dirsOk) {
    Write-Host "  [PASS] All directories exist" -ForegroundColor Green
    $PASS_COUNT++
} else {
    Write-Host "  [FAIL] Some directories missing" -ForegroundColor Red
    $FAIL_COUNT++
}
Write-Host ""

# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════════

Write-Host "═══════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "           VERIFICATION SUMMARY" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""
Write-Host "  PASS: $PASS_COUNT" -ForegroundColor Green
Write-Host "  FAIL: $FAIL_COUNT" -ForegroundColor $(if ($FAIL_COUNT -gt 0) { "Red" } else { "Green" })
Write-Host ""

if ($FAIL_COUNT -eq 0) {
    Write-Host "╔═══════════════════════════════════════════════════════════════════╗" -ForegroundColor Green
    Write-Host "║                    ✅ ALL TESTS PASSED                            ║" -ForegroundColor Green
    Write-Host "║                                                                   ║" -ForegroundColor Green
    Write-Host "║  System is ready! Start pump_detector:                            ║" -ForegroundColor Green
    Write-Host "║  python scripts\pump_detector.py --top 20                         ║" -ForegroundColor Green
    Write-Host "╚═══════════════════════════════════════════════════════════════════╝" -ForegroundColor Green
    exit 0
} else {
    Write-Host "╔═══════════════════════════════════════════════════════════════════╗" -ForegroundColor Red
    Write-Host "║                    ❌ SOME TESTS FAILED                           ║" -ForegroundColor Red
    Write-Host "║                                                                   ║" -ForegroundColor Red
    Write-Host "║  Fix the issues above, then run verify.ps1 again                  ║" -ForegroundColor Red
    Write-Host "║  Or run install.ps1 if not done yet                               ║" -ForegroundColor Red
    Write-Host "╚═══════════════════════════════════════════════════════════════════╝" -ForegroundColor Red
    exit 1
}
