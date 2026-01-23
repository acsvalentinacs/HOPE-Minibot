# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-23 12:00:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-23 12:00:00 UTC
# === END SIGNATURE ===
#
# Backfill AI Signature Created fields from git history.
# Replaces placeholder "Unknown (legacy)" with actual git author/date.
#
# Usage:
#   .\tools\backfill_ai_signature_created.ps1 -FromGitDiff
#   .\tools\backfill_ai_signature_created.ps1 -Files core/foo.py,scripts/bar.py

param(
    [string[]]$Files = @(),
    [switch]$FromGitDiff
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ROOT = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $ROOT

function Get-AuditableFromGitDiff {
    $changed = & git diff --name-only
    if ($LASTEXITCODE -ne 0) { throw "git diff failed" }

    $out = @()
    foreach ($p in $changed) {
        if (-not $p) { $p = "" }
        $p = $p.Trim().Replace("\", "/")
        if (-not $p) { continue }

        $top = $p.Split("/")[0]
        if ($p.EndsWith(".py")) {
            if ($top -in @("core","scripts","tools")) { $out += $p }
        } elseif ($p.EndsWith(".ps1")) {
            if ($top -eq "tools") { $out += $p }
        }
    }
    return $out
}

function Get-FileCreatedInfo([string]$Path) {
    # earliest add commit (diff-filter=A) -> take last line (git log outputs newest first)
    $lines = & git log --follow --diff-filter=A --format="%an|%ad" --date=format:"%Y-%m-%d %H:%M:%S UTC" -- $Path 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $lines) { return $null }

    $line = ($lines | Select-Object -Last 1)
    $parts = $line.Split("|", 2)
    if ($parts.Count -ne 2) { return $null }

    return [pscustomobject]@{
        Author = $parts[0].Trim()
        Date   = $parts[1].Trim()
    }
}

function Update-SignatureCreatedFields([string]$RelPath, [string]$Author, [string]$Date) {
    $fsPath = Join-Path $ROOT ($RelPath.Replace("/", "\"))
    if (-not (Test-Path -LiteralPath $fsPath)) { throw "Missing file: $RelPath" }

    $raw = Get-Content -LiteralPath $fsPath -Raw -Encoding UTF8

    # Require an existing signature block
    if ($raw -notmatch "(?m)^# === AI SIGNATURE ===\s*$") {
        throw "No AI SIGNATURE block found in: $RelPath"
    }

    # Replace Created by/at lines inside the block
    # Note: Using explicit replacement to avoid PowerShell $1 issues
    $raw2 = $raw `
        -replace "(?m)^# Created by:\s*.*$", "# Created by: $Author" `
        -replace "(?m)^# Created at:\s*.*$", "# Created at: $Date"

    if ($raw2 -eq $raw) {
        Write-Host "SKIP (no changes): $RelPath" -ForegroundColor DarkGray
        return
    }

    Set-Content -LiteralPath $fsPath -Value $raw2 -Encoding UTF8
    Write-Host "UPDATED: $RelPath  (Created by/at from git)" -ForegroundColor Green
}

if ($FromGitDiff) {
    $Files = Get-AuditableFromGitDiff
}

if (-not $Files -or $Files.Count -eq 0) {
    Write-Host "No files provided. Use -FromGitDiff or pass -Files ..." -ForegroundColor Yellow
    exit 0
}

$failed = 0
foreach ($f in $Files) {
    try {
        $info = Get-FileCreatedInfo $f
        if (-not $info) { throw "Cannot determine created info from git (no add commit?)" }

        Update-SignatureCreatedFields -RelPath $f -Author $info.Author -Date $info.Date
    } catch {
        $failed++
        Write-Host "FAIL: $f :: $($_.Exception.Message)" -ForegroundColor Red
    }
}

if ($failed -gt 0) { exit 1 }
exit 0
