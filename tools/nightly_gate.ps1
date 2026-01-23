# === AI SIGNATURE ===
# Created by: GitHub Copilot
# Created at: 2026-01-23 12:00:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-23 13:00:00 UTC
# === END SIGNATURE ===

param(
    [ValidateSet('Dev','Release','All')]
    [string]$Mode = 'All',
    [switch]$AllowOffline
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Fail([string]$Msg) {
    Write-Host "[FAIL] $Msg" -ForegroundColor Red
    throw $Msg
}

function Step([string]$Name, [scriptblock]$Action) {
    Write-Host "==> $Name" -ForegroundColor Cyan
    try {
        & $Action
        if ($LASTEXITCODE -ne 0) { Fail "$Name (exit=$LASTEXITCODE)" }
        Write-Host "[OK] $Name" -ForegroundColor Green
    } catch {
        Write-Host "[FAIL] $Name" -ForegroundColor Red
        throw
    }
}

$ROOT = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $ROOT

$PY = Join-Path $ROOT ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $PY)) { Fail "VENV_NOT_FOUND: $PY" }

Write-Host "========================================" -ForegroundColor Yellow
Write-Host "  HOPE NIGHTLY GATE v1.2 (fail-closed)" -ForegroundColor Yellow
Write-Host "  Mode: $Mode" -ForegroundColor Yellow
Write-Host "  ROOT: $ROOT" -ForegroundColor Yellow
Write-Host "  PY:   $PY" -ForegroundColor Yellow
Write-Host "  Offline: $AllowOffline" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow

Step "Python sanity" {
    & $PY -c "import sys; print('Python:', sys.version.split()[0], sys.executable)"
}

# Make SyntaxWarning fail the build during compilation
Step "compileall (warnings-as-errors: SyntaxWarning)" {
    & $PY -W error::SyntaxWarning -m compileall -q -f core scripts tools
}

Step "AllowList audit" {
    & $PY tools\audit_allowlist.py --root $ROOT --file "AllowList.txt"
}

Step "Execution protocol audit" {
    & $PY tools\audit_execution_protocol.py --root $ROOT --file "CLAUDE.md"
}

Step "Cmdline SSoT audit" {
    & $PY tools\audit_cmdline_ssot.py --root $ROOT
}

function RunSignatureAudit([string]$EffectiveMode) {
    if ($EffectiveMode -eq 'Release') {

        Step "Git status (Release): only STAGED changes allowed; no UNSTAGED/UNTRACKED" {
            $lines = & git status --porcelain 2>$null
            if ($LASTEXITCODE -ne 0) { Fail "git status failed (git missing or not a repo)" }

            foreach ($l in $lines) {
                if (-not $l) { $l = "" }
                $s = $l.TrimEnd()
                if (-not $s) { continue }

                # Untracked files are forbidden in Release
                if ($s.StartsWith("??")) {
                    Fail "Release forbids untracked files: $s"
                }

                # Porcelain format: XY <path>
                # Y != ' ' means unstaged changes exist -> forbidden
                if ($s.Length -ge 2) {
                    $Y = $s[1]
                    if ($Y -ne ' ') {
                        Fail "Release forbids unstaged changes: $s"
                    }
                }
            }
        }

        Step "AI signature audit (Release: staged git-diff)" {
            & $PY tools\audit_ai_signature.py --root $ROOT --git-diff --staged
        }
    } else {
        Step "AI signature audit (Dev: git-diff)" {
            & $PY tools\audit_ai_signature.py --root $ROOT --git-diff
        }
    }
}

Step "JSONL self-test" { & $PY -m core.jsonl_sha }

Step "JSONL stress + verify" {
    $out = Join-Path $ROOT "state\stress\nightly_gate.jsonl"
    $dir = Split-Path $out -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    & $PY tools\jsonl_stress.py --procs 4 --lines 50 --out $out
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $PY tools\jsonl_verify.py --in $out
}

if ($Mode -eq 'All') {
    RunSignatureAudit 'Dev'
    RunSignatureAudit 'Release'
} else {
    RunSignatureAudit $Mode
}

Step "pip outdated report" { & $PY -m pip list --outdated }

if (-not $AllowOffline) {
    Step "Market scanner" { & $PY -m core.market_scanner }
    Step "Telegram publisher dry-run" { & $PY -m core.telegram_publisher --dry-run }
} else {
    Write-Host "  (Skipping network operations: -AllowOffline)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  HOPE NIGHTLY GATE: PASS" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
exit 0
