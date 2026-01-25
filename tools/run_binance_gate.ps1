# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-25 11:00:00 UTC
# === END SIGNATURE ===
<#
.SYNOPSIS
    Binance Online Gate Runner - Zero human factor automation.

.DESCRIPTION
    Performs complete gate check:
    1. Loads BINANCE_* from .env into Process env (no value printing)
    2. Runs pytest test_binance_online_gate.py
    3. Finds and validates last report
    4. Verifies SHA256 match
    5. Prints verdict

.PARAMETER SkipPrivate
    Skip private test (run only public endpoint test)

.PARAMETER TestnetMode
    Use testnet endpoint instead of mainnet

.EXAMPLE
    .\tools\run_binance_gate.ps1
    .\tools\run_binance_gate.ps1 -SkipPrivate
    .\tools\run_binance_gate.ps1 -TestnetMode
#>

param(
    [switch]$SkipPrivate,
    [switch]$TestnetMode
)

$ErrorActionPreference = "Stop"

# Paths
$ProjectRoot = "C:\Users\kirillDev\Desktop\TradingBot\minibot"
$PythonExe = "C:\Users\kirillDev\Desktop\TradingBot\.venv\Scripts\python.exe"
$SecretsPath = "C:\secrets\hope\.env"
$AuditDir = Join-Path $ProjectRoot "state\audit\binance_online_gate"
$TestFile = Join-Path $ProjectRoot "tests\test_binance_online_gate.py"

Write-Host "=" * 60 -ForegroundColor Cyan
Write-Host "BINANCE ONLINE GATE RUNNER" -ForegroundColor Cyan
Write-Host "=" * 60 -ForegroundColor Cyan

# Step 1: Verify prerequisites
Write-Host "`n[1/5] Verifying prerequisites..." -ForegroundColor Yellow

if (-not (Test-Path $PythonExe)) {
    Write-Host "FAIL: Python not found at $PythonExe" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $TestFile)) {
    Write-Host "FAIL: Test file not found at $TestFile" -ForegroundColor Red
    exit 1
}

$pythonVersion = & $PythonExe -V 2>&1
Write-Host "  Python: $pythonVersion" -ForegroundColor Green

# Step 2: Load BINANCE_* env vars
Write-Host "`n[2/5] Loading BINANCE_* environment variables..." -ForegroundColor Yellow

if (-not (Test-Path $SecretsPath)) {
    Write-Host "  WARNING: Secrets file not found, private test will SKIP" -ForegroundColor Yellow
} else {
    $lines = Get-Content -LiteralPath $SecretsPath -ErrorAction SilentlyContinue
    $loadedCount = 0
    foreach ($line in $lines) {
        $t = $line.Trim()
        if (-not $t) { continue }
        if ($t.StartsWith("#")) { continue }
        $eq = $t.IndexOf("=")
        if ($eq -lt 1) { continue }

        $name = $t.Substring(0, $eq).Trim()
        if ($name -notmatch "^BINANCE_") { continue }

        $value = $t.Substring($eq + 1)
        [Environment]::SetEnvironmentVariable($name, $value, "Process")
        $loadedCount++
    }
    Write-Host "  Loaded $loadedCount BINANCE_* variables (values hidden)" -ForegroundColor Green
}

# Show loaded keys (Present/Length only)
Write-Host "`n  BINANCE_* Status:" -ForegroundColor Cyan
Get-ChildItem Env: | Where-Object Name -like "BINANCE_*" |
    Sort-Object Name |
    ForEach-Object {
        Write-Host "    $($_.Name): Present=True, Length=$($_.Value.Length)"
    }

# Set testnet mode if requested
if ($TestnetMode) {
    [Environment]::SetEnvironmentVariable("BINANCE_BASE_URL", "https://testnet.binance.vision", "Process")
    Write-Host "  TESTNET MODE ENABLED" -ForegroundColor Yellow
}

# Step 3: Run pytest
Write-Host "`n[3/5] Running pytest..." -ForegroundColor Yellow

$pytestArgs = @("-m", "pytest", "-q", $TestFile, "-vv")
if ($SkipPrivate) {
    $pytestArgs += @("-m", "not private")
    Write-Host "  (Skipping private test)" -ForegroundColor Yellow
}

Set-Location $ProjectRoot
$exitCode = 0
try {
    & $PythonExe @pytestArgs
    $exitCode = $LASTEXITCODE
} catch {
    Write-Host "  ERROR: pytest failed with exception: $_" -ForegroundColor Red
    $exitCode = 1
}

# Step 4: Find and validate last report
Write-Host "`n[4/5] Validating evidence pack..." -ForegroundColor Yellow

$lastDir = Get-ChildItem $AuditDir -Directory -ErrorAction SilentlyContinue |
    Sort-Object Name -Descending |
    Select-Object -First 1

if (-not $lastDir) {
    Write-Host "  FAIL: No report directory found in $AuditDir" -ForegroundColor Red
    exit 1
}

$reportPath = Join-Path $lastDir.FullName "report.json"
$sha256Path = Join-Path $lastDir.FullName "report.json.sha256"

if (-not (Test-Path $reportPath)) {
    Write-Host "  FAIL: report.json not found" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $sha256Path)) {
    Write-Host "  FAIL: report.json.sha256 not found" -ForegroundColor Red
    exit 1
}

Write-Host "  Report: $reportPath" -ForegroundColor Green
Write-Host "  SHA256 file: $sha256Path" -ForegroundColor Green

# Step 5: Verify SHA256
Write-Host "`n[5/5] Verifying SHA256..." -ForegroundColor Yellow

$computedHash = (Get-FileHash $reportPath -Algorithm SHA256).Hash.ToLower()
$storedHash = (Get-Content $sha256Path -Raw).Trim().ToLower()

Write-Host "  Computed: $computedHash"
Write-Host "  Stored:   $storedHash"

if ($computedHash -eq $storedHash) {
    Write-Host "  SHA256 MATCH: OK" -ForegroundColor Green
} else {
    Write-Host "  SHA256 MISMATCH: FAIL" -ForegroundColor Red
    exit 1
}

# Parse and show verdict
$report = Get-Content $reportPath -Raw | ConvertFrom-Json
$verdict = $report.verdict

Write-Host "`n" + ("=" * 60) -ForegroundColor Cyan
if ($verdict -eq "PASS") {
    Write-Host "FINAL VERDICT: PASS" -ForegroundColor Green
} else {
    Write-Host "FINAL VERDICT: FAIL" -ForegroundColor Red
}
Write-Host "=" * 60 -ForegroundColor Cyan

# Show summary
Write-Host "`nSummary:"
Write-Host "  Public:  ok=$($report.public.ok), status=$($report.public.status_code), latency=$($report.public.latency_ms)ms, attempts=$($report.public.attempts)"
if ($report.private.attempted) {
    Write-Host "  Private: ok=$($report.private.ok), status=$($report.private.status_code), latency=$($report.private.latency_ms)ms, attempts=$($report.private.attempts)"
} else {
    Write-Host "  Private: SKIPPED (reason: $($report.private.skipped_reason))"
}

exit $(if ($verdict -eq "PASS") { 0 } else { 1 })
