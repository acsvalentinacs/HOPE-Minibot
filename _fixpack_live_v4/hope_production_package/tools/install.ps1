# ══════════════════════════════════════════════════════════════════════════════
# HOPE AI v4.0 - INSTALL SCRIPT
# Автоматическая интеграция всех файлов
# ══════════════════════════════════════════════════════════════════════════════
# Использование: .\install.ps1
# ══════════════════════════════════════════════════════════════════════════════

$ErrorActionPreference = "Stop"
$VerbosePreference = "Continue"

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

$TARGET_DIR = "C:\Users\kirillDev\Desktop\TradingBot\minibot"
$BACKUP_DIR = "$TARGET_DIR\backups\$(Get-Date -Format 'yyyy-MM-dd_HH-mm-ss')"
$SOURCE_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "═══════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "           HOPE AI v4.0 - INSTALL SCRIPT" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""
Write-Host "Target: $TARGET_DIR" -ForegroundColor Yellow
Write-Host "Backup: $BACKUP_DIR" -ForegroundColor Yellow
Write-Host "Source: $SOURCE_DIR" -ForegroundColor Yellow
Write-Host ""

# ══════════════════════════════════════════════════════════════════════════════
# STEP 1: CREATE BACKUP
# ══════════════════════════════════════════════════════════════════════════════

Write-Host "[STEP 1] Creating backup..." -ForegroundColor Green
New-Item -ItemType Directory -Path $BACKUP_DIR -Force | Out-Null

$filesToBackup = @(
    "scripts\pump_detector.py",
    "core\signal_gate.py",
    "core\adaptive_tp_engine.py",
    "config\signal_filter_rules.json"
)

foreach ($file in $filesToBackup) {
    $fullPath = Join-Path $TARGET_DIR $file
    if (Test-Path $fullPath) {
        $backupPath = Join-Path $BACKUP_DIR $file
        $backupDir = Split-Path -Parent $backupPath
        New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
        Copy-Item $fullPath $backupPath -Force
        Write-Host "  Backed up: $file" -ForegroundColor Gray
    }
}

Write-Host "  [OK] Backup created" -ForegroundColor Green
Write-Host ""

# ══════════════════════════════════════════════════════════════════════════════
# STEP 2: STOP RUNNING PROCESSES (TARGETED)
# ══════════════════════════════════════════════════════════════════════════════

Write-Host "[STEP 2] Stopping running processes (targeted)..." -ForegroundColor Green

$processes = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | 
    Where-Object { $_.CommandLine -match "pump_detector|run_live|autotrader|minibot" }

if ($processes) {
    foreach ($proc in $processes) {
        Write-Host "  Stopping PID=$($proc.ProcessId): $($proc.CommandLine.Substring(0, [Math]::Min(60, $proc.CommandLine.Length)))..." -ForegroundColor Yellow
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
    Write-Host "  [OK] Processes stopped" -ForegroundColor Green
} else {
    Write-Host "  No matching processes found" -ForegroundColor Gray
}
Write-Host ""

# ══════════════════════════════════════════════════════════════════════════════
# STEP 3: CREATE DIRECTORIES
# ══════════════════════════════════════════════════════════════════════════════

Write-Host "[STEP 3] Creating directories..." -ForegroundColor Green

$dirs = @(
    "core",
    "execution",
    "learning",
    "telegram",
    "config",
    "rules",
    "state\trades",
    "state\ai",
    "logs"
)

foreach ($dir in $dirs) {
    $fullPath = Join-Path $TARGET_DIR $dir
    if (-not (Test-Path $fullPath)) {
        New-Item -ItemType Directory -Path $fullPath -Force | Out-Null
        Write-Host "  Created: $dir" -ForegroundColor Gray
    }
}
Write-Host "  [OK] Directories ready" -ForegroundColor Green
Write-Host ""

# ══════════════════════════════════════════════════════════════════════════════
# STEP 4: COPY FILES
# ══════════════════════════════════════════════════════════════════════════════

Write-Host "[STEP 4] Copying files..." -ForegroundColor Green

$filesToCopy = @(
    @{ Source = "core\signal_gate.py"; Target = "core\signal_gate.py" },
    @{ Source = "core\adaptive_tp_engine.py"; Target = "core\adaptive_tp_engine.py" },
    @{ Source = "execution\binance_oco_executor.py"; Target = "execution\binance_oco_executor.py" },
    @{ Source = "learning\trade_outcome_logger.py"; Target = "learning\trade_outcome_logger.py" },
    @{ Source = "telegram\signals_inbox.py"; Target = "telegram\signals_inbox.py" },
    @{ Source = "config\signal_filter_rules.json"; Target = "config\signal_filter_rules.json" },
    @{ Source = "config\live_trade_policy.py"; Target = "config\live_trade_policy.py" },
    @{ Source = "rules\signal_filters.py"; Target = "rules\signal_filters.py" }
)

$copiedCount = 0
foreach ($file in $filesToCopy) {
    $sourcePath = Join-Path $SOURCE_DIR $file.Source
    $targetPath = Join-Path $TARGET_DIR $file.Target
    
    if (Test-Path $sourcePath) {
        $targetDir = Split-Path -Parent $targetPath
        if (-not (Test-Path $targetDir)) {
            New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
        }
        Copy-Item $sourcePath $targetPath -Force
        Write-Host "  Copied: $($file.Target)" -ForegroundColor Gray
        $copiedCount++
    } else {
        Write-Host "  [SKIP] Not found: $($file.Source)" -ForegroundColor Yellow
    }
}

Write-Host "  [OK] Copied $copiedCount files" -ForegroundColor Green
Write-Host ""

# ══════════════════════════════════════════════════════════════════════════════
# STEP 5: PATCH pump_detector.py
# ══════════════════════════════════════════════════════════════════════════════

Write-Host "[STEP 5] Patching pump_detector.py..." -ForegroundColor Green

$pumpDetectorPath = Join-Path $TARGET_DIR "scripts\pump_detector.py"

if (Test-Path $pumpDetectorPath) {
    $content = Get-Content $pumpDetectorPath -Raw -Encoding UTF8
    
    # Check if already patched
    if ($content -match "HARD TELEGRAM FILTER - BEGIN") {
        Write-Host "  Already patched, skipping" -ForegroundColor Gray
    } else {
        # Find _emit_signal function and inject filter
        $pattern = '(?m)^(\s*)async def _emit_signal\s*\([^)]*\)\s*:'
        
        if ($content -match $pattern) {
            $indent = $Matches[1]
            $bodyIndent = $indent + "    "
            
            $injectBlock = @"

$bodyIndent# ═══════════════════════════════════════════════════════════════════
$bodyIndent# HARD TELEGRAM FILTER - BEGIN
$bodyIndent# Blocks: MICRO/TEST_ACTIVITY/SCALP and delta < 10% (strict, fail-closed)
$bodyIndent# ═══════════════════════════════════════════════════════════════════
${bodyIndent}_delta = float(signal.get("delta_pct", 0) or 0)
${bodyIndent}_type = str(signal.get("type", "") or "")
${bodyIndent}_sym = str(signal.get("symbol", "") or "")

$bodyIndent# BLOCK: MICRO, TEST_ACTIVITY, SCALP
${bodyIndent}if _type in ("MICRO", "TEST_ACTIVITY", "SCALP"):
$bodyIndent    return  # NO TELEGRAM

$bodyIndent# BLOCK: delta < 10%
${bodyIndent}if _delta < 10.0:
$bodyIndent    return  # NO TELEGRAM
$bodyIndent# HARD TELEGRAM FILTER - END
$bodyIndent# ════════════════════════════════════════════════
"@
            
            # Insert after function definition line
            $newContent = $content -replace $pattern, "`$0$injectBlock"
            
            # Write atomically
            $tempPath = "$pumpDetectorPath.tmp"
            Set-Content -Path $tempPath -Value $newContent -Encoding UTF8 -NoNewline
            Move-Item -Path $tempPath -Destination $pumpDetectorPath -Force
            
            Write-Host "  [OK] Patched pump_detector.py" -ForegroundColor Green
        } else {
            Write-Host "  [WARN] Could not find _emit_signal function" -ForegroundColor Yellow
        }
    }
} else {
    Write-Host "  [WARN] pump_detector.py not found" -ForegroundColor Yellow
}
Write-Host ""

# ══════════════════════════════════════════════════════════════════════════════
# STEP 6: VERIFY SYNTAX
# ══════════════════════════════════════════════════════════════════════════════

Write-Host "[STEP 6] Verifying Python syntax..." -ForegroundColor Green

$pyFiles = Get-ChildItem -Path $TARGET_DIR -Filter "*.py" -Recurse | 
    Where-Object { $_.FullName -notmatch "\\backups\\" -and $_.FullName -notmatch "__pycache__" }

$syntaxErrors = 0
foreach ($file in $pyFiles) {
    $result = python -m py_compile $file.FullName 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [FAIL] $($file.Name): $result" -ForegroundColor Red
        $syntaxErrors++
    }
}

if ($syntaxErrors -eq 0) {
    Write-Host "  [OK] All files syntax OK" -ForegroundColor Green
} else {
    Write-Host "  [FAIL] $syntaxErrors files with syntax errors" -ForegroundColor Red
}
Write-Host ""

# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════════

Write-Host "═══════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "           INSTALL COMPLETE" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Run: .\verify.ps1" -ForegroundColor White
Write-Host "  2. Start: python scripts\pump_detector.py --top 20" -ForegroundColor White
Write-Host "  3. Check Telegram for spam (should be NONE)" -ForegroundColor White
Write-Host ""
Write-Host "Rollback if needed:" -ForegroundColor Yellow
Write-Host "  Copy files from: $BACKUP_DIR" -ForegroundColor White
Write-Host ""
