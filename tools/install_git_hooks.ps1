# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-22 19:30:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-22 23:00:00 UTC
# === END SIGNATURE ===

# Install HOPE Git hooks (deletion protection)
# Blocks any commit that deletes files in protected paths
# SSoT: ROOT computed from script location, not hardcoded

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# SSoT: Compute ROOT from script location (parent of tools/)
$ROOT = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

# Find .git directory (may be in parent)
$repo = $ROOT
while (-not (Test-Path (Join-Path $repo '.git'))) {
    $parent = Split-Path $repo -Parent
    if ($parent -eq $repo -or [string]::IsNullOrEmpty($parent)) {
        throw "FAIL-CLOSED: .git not found above $ROOT"
    }
    $repo = $parent
}

$gitDir = Join-Path $repo '.git'
$hooksDir = Join-Path $gitDir 'hooks'

# Ensure hooks directory exists
if (-not (Test-Path $hooksDir)) {
    New-Item -ItemType Directory -Path $hooksDir -Force | Out-Null
}

# Source hook file
$srcHook = Join-Path $ROOT 'tools\git-hooks\pre-commit'
$dstHook = Join-Path $hooksDir 'pre-commit'

if (Test-Path -LiteralPath $srcHook) {
    Copy-Item -Force -LiteralPath $srcHook -Destination $dstHook
    Write-Host "Installed pre-commit hook from: $srcHook"
} else {
    # Create inline if source doesn't exist
    $hookContent = @'
#!/bin/sh
# HOPE pre-commit hook: block deletions in protected paths (fail-closed)
set -e

deleted=$(git diff --cached --name-status --diff-filter=D 2>/dev/null | awk '{print $2}')
[ -z "$deleted" ] && exit 0

is_protected() {
    case "$1" in
        core/*|scripts/*|tools/*|state/*|data/*|CLAUDE.md) return 0 ;;
        *) return 1 ;;
    esac
}

blocked=""
for path in $deleted; do
    p=$(printf '%s' "$path" | tr '\\' '/')
    if is_protected "$p"; then
        blocked="$blocked
  - $p"
    fi
done

if [ -n "$blocked" ]; then
    echo "FAIL-CLOSED: deletion blocked by HOPE policy." >&2
    echo "$blocked" >&2
    echo "" >&2
    echo "To delete protected files, adjust hook policy (review-required)." >&2
    exit 1
fi

exit 0
'@
    Set-Content -Encoding ASCII -NoNewline -Path $dstHook -Value $hookContent
    Write-Host "Created pre-commit hook inline at: $dstHook"
}

Write-Host "PASS: Git hooks installed at $hooksDir"
