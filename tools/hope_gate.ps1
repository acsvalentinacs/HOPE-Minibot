# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-22 19:00:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-23 20:00:00 UTC
# === END SIGNATURE ===

# HOPE GATE v5.4 (fail-closed, git-diff audit, AllowList, Execution protocol, Baseline freeze, No deletions, .env policy)
# Fixed: CLAUDE.md path uses parent dir; removed missing chat_shell.py; added cmdline_ssot/market_scanner modules
# SSoT: ROOT computed from script location, not hardcoded
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\tools\hope_gate.ps1
#   powershell -ExecutionPolicy Bypass -File .\tools\hope_gate.ps1 -Mode Release
#   powershell -ExecutionPolicy Bypass -File .\tools\hope_gate.ps1 -AllowOffline

param(
    [ValidateSet('Dev', 'Release')]
    [string]$Mode = 'Dev',
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
        Write-Host "[OK] $Name" -ForegroundColor Green
    } catch {
        Write-Host "[FAIL] $Name" -ForegroundColor Red
        throw $_
    }
}

# SSoT: Compute ROOT from script location (parent of tools/)
$ROOT = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

# Validate ROOT structure
if (-not (Test-Path (Join-Path $ROOT "core"))) { Fail "Invalid ROOT: missing core/ at $ROOT" }
if (-not (Test-Path (Join-Path $ROOT "state"))) { Fail "Invalid ROOT: missing state/ at $ROOT" }

Set-Location -LiteralPath $ROOT

# Find Python (venv required)
$PY = Join-Path $ROOT ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $PY)) {
    Fail "VENV_NOT_FOUND: Create .venv at $ROOT\.venv"
}

Write-Host "========================================" -ForegroundColor Yellow
Write-Host "  HOPE GATE v5.4 (fail-closed)" -ForegroundColor Yellow
Write-Host "  Mode: $Mode" -ForegroundColor Yellow
Write-Host "  ROOT: $ROOT" -ForegroundColor Yellow
Write-Host "  PY:   $PY" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow

Step "Python sanity" {
    & $PY -c "import sys; print('Python:', sys.version.split()[0], sys.executable)"
    if ($LASTEXITCODE -ne 0) { Fail "Python check failed" }
}

Step "Policy preflight (HOPE-LAW-001)" {
    $policyPath = Join-Path $ROOT "core\policy\policy.json"
    if (-not (Test-Path $policyPath)) { Fail "Missing: core/policy/policy.json" }

    & $PY -c "from core.policy.loader import load_policy; from pathlib import Path; p=load_policy(Path('core/policy/policy.json'), 'gate'); print(f'Policy v{p.policy_version} loaded')"
    if ($LASTEXITCODE -ne 0) { Fail "Policy preflight failed (HOPE-LAW-001)" }
}

Step "Policy gate scan (secrets + forbidden phrases)" {
    $gateScript = Join-Path $ROOT "tools\policy_gate.py"
    if (Test-Path $gateScript) {
        & $PY -m tools.policy_gate --secrets-only
        if ($LASTEXITCODE -ne 0) { Fail "Policy gate failed (secret patterns detected)" }
    } else {
        Write-Host "  (policy_gate.py not found, skipping)"
    }
}

Step "AllowList audit (fail-closed)" {
    $allowlistFile = Join-Path $ROOT "AllowList.txt"
    $auditScript = Join-Path $ROOT "tools\audit_allowlist.py"

    if (-not (Test-Path $allowlistFile)) { Fail "Missing: AllowList.txt" }
    if (-not (Test-Path $auditScript)) { Fail "Missing: tools\audit_allowlist.py" }

    & $PY $auditScript --root $ROOT --file "AllowList.txt"
    if ($LASTEXITCODE -ne 0) { Fail "AllowList audit failed (wildcards/schemes/paths forbidden)" }
}

Step "Execution protocol audit (CLAUDE.md)" {
    $auditScript = Join-Path $ROOT "tools\audit_execution_protocol.py"

    if (-not (Test-Path $auditScript)) { Fail "Missing: tools\audit_execution_protocol.py" }

    # CLAUDE.md is in parent directory (TradingBot), not minibot
    $claudeRoot = (Resolve-Path (Join-Path $ROOT "..")).Path
    & $PY $auditScript --root $claudeRoot --file "CLAUDE.md"
    if ($LASTEXITCODE -ne 0) { Fail "HOPE EXECUTION PROTOCOL missing or not first H1 in CLAUDE.md" }
}

Step "Baseline locks audit (BASELINE FREEZE, fail-closed)" {
    $auditScript = Join-Path $ROOT "tools\audit_baseline_locks.py"
    $locksFile = Join-Path $ROOT "tools\baseline_locks.json"

    if (-not (Test-Path $auditScript)) { Fail "Missing: tools\audit_baseline_locks.py" }
    if (-not (Test-Path $locksFile)) { Fail "Missing: tools\baseline_locks.json" }

    & $PY $auditScript --root $ROOT --file "tools/baseline_locks.json"
    if ($LASTEXITCODE -ne 0) { Fail "Baseline locks audit failed (BASELINE FREEZE violation)" }
}

Step "Import contract: core modules" {
    & $PY -c "import core.atomic_io, core.cmdline_ssot, core.contracts, core.jsonl_sha, core.market_scanner, core.telegram_publisher; print('IMPORT_OK')"
    if ($LASTEXITCODE -ne 0) { Fail "Core import failed" }
}

Step "py_compile: key modules" {
    & $PY -m py_compile `
        core\cmdline_ssot.py `
        core\atomic_io.py `
        core\contracts.py `
        core\jsonl_sha.py `
        core\market_scanner.py `
        core\telegram_publisher.py `
        scripts\morning_scan.py `
        tools\audit_ai_signature.py `
        tools\audit_allowlist.py `
        tools\audit_baseline_locks.py `
        tools\audit_execution_protocol.py `
        tools\audit_no_deletions.py `
        tools\audit_env_policy.py `
        tools\audit_cmdline_ssot.py `
        tools\jsonl_stress.py `
        tools\jsonl_verify.py
    if ($LASTEXITCODE -ne 0) { Fail "py_compile failed" }
}

Step "SSoT cmdline: GetCommandLineW sha256" {
    & $PY -c "from core.cmdline_ssot import get_cmdline_sha256; h=get_cmdline_sha256(); assert h.startswith('sha256:') and len(h)==71, f'bad:{h}'; print(h[:40]+'...')"
    if ($LASTEXITCODE -ne 0) { Fail "cmdline_ssot check failed" }
}

Step "JSONL writer self-test (inter-process lock)" {
    & $PY -m core.jsonl_sha
    if ($LASTEXITCODE -ne 0) { Fail "jsonl_sha self-test failed" }
}

Step "JSONL stress test (4 procs x 50 lines)" {
    $stressOut = Join-Path $ROOT "state\stress\gate_test.jsonl"
    $stressDir = Split-Path $stressOut -Parent
    if (-not (Test-Path $stressDir)) {
        New-Item -ItemType Directory -Path $stressDir -Force | Out-Null
    }

    # Run stress test (reduced for gate: 4 procs x 50 lines = 200 lines)
    & $PY (Join-Path $ROOT "tools\jsonl_stress.py") --procs 4 --lines 50 --out $stressOut
    if ($LASTEXITCODE -ne 0) { Fail "JSONL stress test failed" }

    # Verify output
    & $PY (Join-Path $ROOT "tools\jsonl_verify.py") --in $stressOut
    if ($LASTEXITCODE -ne 0) { Fail "JSONL verify failed" }
}

Step "Cmdline SSoT audit (ban sys.argv hash)" {
    & $PY (Join-Path $ROOT "tools\audit_cmdline_ssot.py") --root $ROOT
    if ($LASTEXITCODE -ne 0) { Fail "Cmdline SSoT audit failed" }
}

# Release mode requires clean working tree
if ($Mode -eq 'Release') {
    Step "Git status: working tree must be clean (Release mode)" {
        $gitStatus = & git status --porcelain 2>$null
        if ($LASTEXITCODE -ne 0) { Fail "git status failed (not a git repo?)" }
        if ($gitStatus) {
            Write-Host "  Uncommitted changes detected:" -ForegroundColor Red
            $gitStatus | Select-Object -First 10 | ForEach-Object { Write-Host "    $_" }
            Fail "Release mode requires clean working tree (commit or stash changes)"
        }
        Write-Host "  Working tree is clean"
    }
}

Step "AI signature audit (git-diff, fail-closed)" {
    $auditScript = Join-Path $ROOT "tools\audit_ai_signature.py"

    if (-not (Test-Path $auditScript)) { Fail "Missing: tools\audit_ai_signature.py" }

    if ($Mode -eq 'Release') {
        # Release mode: check staged changes with --require-nonempty (no PASS by empty)
        & $PY $auditScript --root $ROOT --git-diff --staged --require-nonempty
        if ($LASTEXITCODE -ne 0) { Fail "AI signature audit failed (Release: staged git-diff --require-nonempty)" }
    } else {
        # Dev mode: check working tree changes (allows SKIP on empty)
        & $PY $auditScript --root $ROOT --git-diff
        if ($LASTEXITCODE -ne 0) { Fail "AI signature audit failed (Dev: git-diff)" }
    }
}

Step "No deletions audit (LAW 3, fail-closed)" {
    $auditScript = Join-Path $ROOT "tools\audit_no_deletions.py"

    if (-not (Test-Path $auditScript)) { Fail "Missing: tools\audit_no_deletions.py" }

    if ($Mode -eq 'Release') {
        # Release mode: check staged changes
        & $PY $auditScript --root $ROOT --staged
        if ($LASTEXITCODE -ne 0) { Fail "No deletions audit failed (LAW 3: deletions/renames require explicit approval)" }
    } else {
        # Dev mode: check working tree
        & $PY $auditScript --root $ROOT
        if ($LASTEXITCODE -ne 0) { Fail "No deletions audit failed (LAW 3: deletions/renames require explicit approval)" }
    }
}

Step ".env policy audit (LAW 4, fail-closed)" {
    $auditScript = Join-Path $ROOT "tools\audit_env_policy.py"

    if (-not (Test-Path $auditScript)) { Fail "Missing: tools\audit_env_policy.py" }

    if ($Mode -eq 'Release') {
        & $PY $auditScript --root $ROOT --staged
        if ($LASTEXITCODE -ne 0) { Fail ".env policy violation (LAW 4: .env immutable; append-only by owner)" }
    } else {
        & $PY $auditScript --root $ROOT
        if ($LASTEXITCODE -ne 0) { Fail ".env policy violation (LAW 4: .env immutable; append-only by owner)" }
    }
}

if ($AllowOffline) {
    Write-Host "  (Skipping network operations: -AllowOffline)" -ForegroundColor Yellow
} else {
    Step "Market scanner: refresh market_intel.json" {
        & $PY -m core.market_scanner
        if ($LASTEXITCODE -ne 0) { Fail "market_scanner failed" }

        $intel = Join-Path $ROOT "state\market_intel.json"
        if (-not (Test-Path $intel)) { Fail "Missing: state\market_intel.json" }
    }

    Step "Validate market_intel contract (snapshot_id + TTL)" {
        $intel = Join-Path $ROOT "state\market_intel.json"
        & $PY -c @"
import json, time, sys
p = r'$intel'
d = json.load(open(p, 'r', encoding='utf-8'))
sid = d.get('market_snapshot_id') or d.get('snapshot_id') or ''
ts = d.get('timestamp') or d.get('ts') or 0
if not isinstance(sid, str) or not sid.startswith('sha256:'):
    raise SystemExit('FAIL-CLOSED: invalid snapshot_id')
if isinstance(ts, (int, float)) and ts > 0:
    age = time.time() - float(ts)
    if age > 300:
        raise SystemExit(f'FAIL-CLOSED: stale_data age={age:.0f}s')
    print(f'snapshot_id: {sid[:32]}... age: {age:.0f}s')
"@
        if ($LASTEXITCODE -ne 0) { Fail "market_intel contract validation failed" }
    }

    Step "Telegram publisher dry-run" {
        & $PY -m core.telegram_publisher --dry-run
        if ($LASTEXITCODE -ne 0) { Fail "telegram_publisher dry-run failed" }
    }
}

Step "Git hooks: install deletion protection" {
    $hookInstaller = Join-Path $ROOT "tools\install_git_hooks.ps1"
    if (Test-Path $hookInstaller) {
        & powershell -ExecutionPolicy Bypass -File $hookInstaller
        if ($LASTEXITCODE -ne 0) { Fail "Git hook installation failed" }
    } else {
        Write-Host "  (install_git_hooks.ps1 not found, skipping)"
    }
}

Step "File count statistics" {
    $exclude = '\\\.git\\|\\\.venv\\|\\__pycache__\\|\\\.pytest_cache\\|\\\.mypy_cache\\|\\\.ruff_cache\\'
    $files = Get-ChildItem -Path $ROOT -Recurse -File -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notmatch $exclude }

    $count = ($files | Measure-Object).Count
    Write-Host "  FILES_TOTAL: $count"

    $top5 = $files | Group-Object Extension | Sort-Object Count -Descending | Select-Object -First 5
    foreach ($g in $top5) {
        Write-Host ("  {0,5} {1}" -f $g.Count, $g.Name)
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  HOPE GATE: PASS" -ForegroundColor Green
Write-Host "  Mode: $Mode" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
exit 0
