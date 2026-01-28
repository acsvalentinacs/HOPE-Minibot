# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T01:00:00Z
# Purpose: Launch 6-hour supertest for HOPE Trading Safety Core
# Security: Fail-closed, logs to state/supertest/
# === END SIGNATURE ===

<#
.SYNOPSIS
    Launch HOPE 6-hour supertest

.DESCRIPTION
    Runs comprehensive tests in a loop for 6 hours:
    - Core module compilation
    - All pytest suites
    - JSONL stress tests
    - AI signature audits
    - Trading Safety Core validation

.PARAMETER DurationHours
    Duration in hours (default: 6)

.PARAMETER AllowOffline
    Skip network tests

.EXAMPLE
    .\run_supertest_6h.ps1
    # Runs 6 hours with network tests

.EXAMPLE
    .\run_supertest_6h.ps1 -DurationHours 1 -AllowOffline
    # Runs 1 hour without network tests
#>

param(
    [float]$DurationHours = 6,
    [switch]$AllowOffline
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# SSoT root
$ROOT = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $ROOT

# Find Python
$PY = Join-Path $ROOT ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $PY)) {
    $PY = "python"
}

Write-Host "========================================" -ForegroundColor Yellow
Write-Host "  HOPE SUPERTEST 6H LAUNCHER" -ForegroundColor Yellow
Write-Host "  Duration: $DurationHours hours" -ForegroundColor Yellow
Write-Host "  Offline:  $AllowOffline" -ForegroundColor Yellow
Write-Host "  Python:   $PY" -ForegroundColor Yellow
Write-Host "  ROOT:     $ROOT" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow
Write-Host ""

$args_list = @(
    "tools/supertest_6h.py"
    "--duration-hours"
    $DurationHours
)

if ($AllowOffline) {
    $args_list += "--allow-offline"
}

& $PY @args_list
$exitCode = $LASTEXITCODE

if ($exitCode -eq 0) {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  SUPERTEST: PASS" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Red
    Write-Host "  SUPERTEST: FAIL (exit=$exitCode)" -ForegroundColor Red
    Write-Host "========================================" -ForegroundColor Red
}

exit $exitCode
