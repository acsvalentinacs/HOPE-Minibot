# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-23 16:40:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-23 17:00:00 UTC
# === END SIGNATURE ===
<#
.SYNOPSIS
    HOPE Full Test - Complete validation from first file to last point.

.DESCRIPTION
    Runs all HOPE validation checks:
    1. py_compile (syntax check)
    2. AllowList lint (with required hosts)
    3. Bootstrap-first verification
    4. Network guard smoke test
    5. Policy gate (secrets/phrases)
    6. Pytest (if tests exist)

.PARAMETER RepoRoot
    Repository root directory (default: current directory)

.PARAMETER PythonExe
    Python executable (default: python)

.PARAMETER SkipNetwork
    Skip network smoke tests

.EXAMPLE
    .\tools\hope_full_test.ps1
    .\tools\hope_full_test.ps1 -RepoRoot C:\path\to\minibot -SkipNetwork
#>

param(
    [string]$RepoRoot = (Get-Location).Path,
    [string]$PythonExe = "python",
    [switch]$SkipNetwork,
    [switch]$Verbose
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Step {
    param([string]$Name)
    Write-Host "`n==> $Name" -ForegroundColor Cyan
}

function Write-Pass {
    param([string]$Msg)
    Write-Host "[PASS] $Msg" -ForegroundColor Green
}

function Write-Fail {
    param([string]$Msg)
    Write-Host "[FAIL] $Msg" -ForegroundColor Red
}

function Write-Skip {
    param([string]$Msg)
    Write-Host "[SKIP] $Msg" -ForegroundColor Yellow
}

# Resolve paths
$RepoRoot = (Resolve-Path $RepoRoot).Path
Set-Location $RepoRoot

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  HOPE FULL TEST" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "RepoRoot: $RepoRoot"
Write-Host "Python:   $PythonExe"

# 1) Compile check
Write-Step "1/6 - Syntax check (py_compile)"
try {
    & $PythonExe -m compileall -q -f core tools 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Compilation errors in core/ or tools/"
        exit 1
    }
    Write-Pass "All Python files compile"
} catch {
    Write-Fail "Compile check failed: $_"
    exit 1
}

# 2) AllowList lint
Write-Step "2/6 - AllowList lint"
$lintScript = Join-Path $RepoRoot "tools\lint_allowlist.py"
$allowlistFile = Join-Path $RepoRoot "AllowList.txt"

if (Test-Path $lintScript) {
    try {
        # Lint all allowlist files (required hosts check is built into linter)
        & $PythonExe -m tools.lint_allowlist
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "AllowList lint failed"
            exit 1
        }
        Write-Pass "AllowList valid"
    } catch {
        Write-Fail "AllowList lint error: $_"
        exit 1
    }
} else {
    # Fallback to lint_allowlist module
    try {
        & $PythonExe -m tools.lint_allowlist
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "AllowList lint failed"
            exit 1
        }
        Write-Pass "AllowList valid"
    } catch {
        Write-Skip "lint_allowlist not found"
    }
}

# 3) Bootstrap-first verification
Write-Step "3/6 - Bootstrap-first verification"
$bootstrapScript = Join-Path $RepoRoot "tools\verify_bootstrap_first.py"

if (Test-Path $bootstrapScript) {
    try {
        & $PythonExe $bootstrapScript --root $RepoRoot
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "Bootstrap-first verification failed"
            exit 1
        }
        Write-Pass "Bootstrap called first in all entrypoints"
    } catch {
        Write-Fail "Bootstrap verification error: $_"
        exit 1
    }
} else {
    Write-Skip "verify_bootstrap_first.py not found"
}

# 4) Network guard smoke test
Write-Step "4/6 - Network guard smoke test"
if ($SkipNetwork) {
    Write-Skip "Network tests skipped (-SkipNetwork)"
} else {
    $smokeScript = Join-Path $RepoRoot "tools\smoke_network_guard.py"
    if (Test-Path $smokeScript) {
        try {
            & $PythonExe $smokeScript
            $exitCode = $LASTEXITCODE
            if ($exitCode -eq 0) {
                Write-Pass "Network guard working"
            } elseif ($exitCode -eq 2) {
                Write-Skip "Bootstrap not available"
            } else {
                Write-Fail "Network guard not working"
                exit 1
            }
        } catch {
            Write-Fail "Smoke test error: $_"
            exit 1
        }
    } else {
        Write-Skip "smoke_network_guard.py not found"
    }
}

# 5) Policy gate
Write-Step "5/6 - Policy gate (secrets/phrases)"
$policyGate = Join-Path $RepoRoot "tools\policy_gate.py"

if (Test-Path $policyGate) {
    try {
        & $PythonExe -m tools.policy_gate --secrets-only
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "Policy gate failed (secrets detected)"
            exit 1
        }
        Write-Pass "No secret patterns in codebase"
    } catch {
        Write-Fail "Policy gate error: $_"
        exit 1
    }
} else {
    Write-Skip "policy_gate.py not found"
}

# 6) Pytest
Write-Step "6/6 - Pytest"
$testsDir = Join-Path $RepoRoot "tests"

if (Test-Path $testsDir) {
    try {
        & $PythonExe -m pytest -q --tb=short
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "Pytest failed"
            exit 1
        }
        Write-Pass "All tests passed"
    } catch {
        Write-Fail "Pytest error: $_"
        exit 1
    }
} else {
    Write-Skip "No tests directory"
}

# Summary
Write-Host "`n========================================" -ForegroundColor Green
Write-Host "  HOPE FULL TEST: PASS" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
exit 0
