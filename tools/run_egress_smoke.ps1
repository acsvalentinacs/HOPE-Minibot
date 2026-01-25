<# === AI SIGNATURE ===
   Created by: Claude (opus-4)
   Created at (UTC): 2026-01-25T12:30:00Z
   Purpose: Smoke test for Egress Policy (read-only, no repo modifications)
   === END SIGNATURE ===
#>

<#
.SYNOPSIS
    Egress Policy Smoke Test - proves ALLOW/DENY behavior at runtime.

.DESCRIPTION
    This script runs Python smoke tests that:
    1. Attempt GET to a host NOT in AllowList (expects DENY)
    2. Attempt GET to api.binance.com (expects ALLOW if in AllowList)
    3. Show last 5 audit log entries

    Does NOT modify git-tracked files. Only writes to staging/history/.

.EXAMPLE
    .\tools\run_egress_smoke.ps1
#>

param(
    [switch]$Verbose
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)

Write-Host ("=" * 60) -ForegroundColor Cyan
Write-Host "EGRESS POLICY SMOKE TEST" -ForegroundColor Cyan
Write-Host ("=" * 60) -ForegroundColor Cyan
Write-Host ""

# Change to project root
Set-Location $ProjectRoot
Write-Host "[INFO] Project root: $ProjectRoot" -ForegroundColor Gray

# Determine Python executable
$PythonExe = Join-Path (Split-Path -Parent $ProjectRoot) ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    $PythonExe = "python"
}
Write-Host "[INFO] Python: $PythonExe" -ForegroundColor Gray
Write-Host ""

# Run Python smoke test
$SmokeScript = Join-Path $ProjectRoot "tools\egress_smoke_test.py"
Write-Host "[RUN] Executing: $SmokeScript" -ForegroundColor Yellow
Write-Host ""

try {
    & $PythonExe $SmokeScript
    $ExitCode = $LASTEXITCODE
}
catch {
    Write-Host "[ERROR] Failed to run smoke test: $_" -ForegroundColor Red
    $ExitCode = 1
}

Write-Host ""
Write-Host ("=" * 60) -ForegroundColor Cyan

# Show audit log path
$AuditPath = Join-Path $ProjectRoot "staging\history\egress_audit.jsonl"
if (Test-Path $AuditPath) {
    Write-Host "[INFO] Audit log: $AuditPath" -ForegroundColor Gray
    $LineCount = (Get-Content $AuditPath | Measure-Object -Line).Lines
    Write-Host "[INFO] Total audit entries: $LineCount" -ForegroundColor Gray
} else {
    Write-Host "[WARN] Audit log not found (no requests made yet)" -ForegroundColor Yellow
}

Write-Host ""
if ($ExitCode -eq 0) {
    Write-Host "[RESULT] SMOKE TEST PASSED" -ForegroundColor Green
} else {
    Write-Host "[RESULT] SMOKE TEST FAILED" -ForegroundColor Red
}

exit $ExitCode
