<# === AI SIGNATURE ===
   Created by: Claude (opus-4)
   Created at (UTC): 2026-01-25T12:30:00Z
   Purpose: Guard script to detect bypasses of Egress Policy (read-only)
   === END SIGNATURE ===
#>

<#
.SYNOPSIS
    Detects direct HTTP library usage that bypasses Egress Policy wrapper.

.DESCRIPTION
    Scans Python files for direct usage of:
    - urllib.request.urlopen
    - requests.get/post/etc.
    - httpx.get/post/etc.
    - aiohttp.ClientSession

    ALLOWED locations (exceptions):
    - core/net/http_client.py (the wrapper itself)
    - tests/ (test files may mock these)

    Returns exit code 1 if violations found, 0 otherwise.

.EXAMPLE
    .\tools\net_policy_grep_guard.ps1
#>

param(
    [switch]$Verbose,
    [switch]$FixSuggestions
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)

Write-Host "=" * 60 -ForegroundColor Cyan
Write-Host "EGRESS POLICY GUARD CHECK" -ForegroundColor Cyan
Write-Host "=" * 60 -ForegroundColor Cyan
Write-Host ""

Set-Location $ProjectRoot
Write-Host "[INFO] Scanning: $ProjectRoot" -ForegroundColor Gray
Write-Host ""

# Patterns to detect (potential bypasses)
$BypassPatterns = @(
    # Direct urllib usage
    "urlopen\s*\(",
    "urllib\.request\.urlopen",
    "urllib\.request\.Request",
    "from\s+urllib\.request\s+import.*urlopen",

    # requests library
    "requests\.get\s*\(",
    "requests\.post\s*\(",
    "requests\.put\s*\(",
    "requests\.delete\s*\(",
    "requests\.head\s*\(",
    "requests\.Session\s*\(",

    # httpx library
    "httpx\.get\s*\(",
    "httpx\.post\s*\(",
    "httpx\.Client\s*\(",
    "httpx\.AsyncClient\s*\(",

    # aiohttp library
    "aiohttp\.ClientSession\s*\(",
    "ClientSession\s*\(\s*\)"
)

# Allowed files (exceptions)
$AllowedPaths = @(
    "core/net/http_client.py",   # The wrapper itself
    "core\\net\\http_client.py", # Windows path
    "tests/",                     # Test files
    "tests\\"                     # Windows path
)

function Test-IsAllowedPath {
    param([string]$FilePath)

    $normalized = $FilePath.Replace("\", "/").ToLower()

    foreach ($allowed in $AllowedPaths) {
        $allowedNorm = $allowed.Replace("\", "/").ToLower()
        if ($normalized -like "*$allowedNorm*") {
            return $true
        }
    }
    return $false
}

# Find all Python files
$PythonFiles = Get-ChildItem -Path $ProjectRoot -Filter "*.py" -Recurse -File |
    Where-Object { $_.FullName -notmatch "\.venv|__pycache__|\.git|staging" }

Write-Host "[INFO] Found $($PythonFiles.Count) Python files to scan" -ForegroundColor Gray
Write-Host ""

$Violations = @()
$ScannedCount = 0

foreach ($file in $PythonFiles) {
    $relativePath = $file.FullName.Substring($ProjectRoot.Length + 1)

    # Skip allowed paths
    if (Test-IsAllowedPath $relativePath) {
        if ($Verbose) {
            Write-Host "  [SKIP] $relativePath (allowed)" -ForegroundColor DarkGray
        }
        continue
    }

    $ScannedCount++
    $content = Get-Content $file.FullName -Raw -ErrorAction SilentlyContinue

    if (-not $content) { continue }

    foreach ($pattern in $BypassPatterns) {
        if ($content -match $pattern) {
            # Find line number
            $lines = Get-Content $file.FullName
            $lineNum = 0
            foreach ($line in $lines) {
                $lineNum++
                if ($line -match $pattern) {
                    $Violations += [PSCustomObject]@{
                        File = $relativePath
                        Line = $lineNum
                        Pattern = $pattern
                        Content = $line.Trim().Substring(0, [Math]::Min(80, $line.Trim().Length))
                    }
                }
            }
        }
    }
}

Write-Host "[INFO] Scanned $ScannedCount files (excluding allowed paths)" -ForegroundColor Gray
Write-Host ""

# Report results
if ($Violations.Count -eq 0) {
    Write-Host "[PASS] No egress policy bypasses detected" -ForegroundColor Green
    Write-Host ""
    Write-Host "All HTTP requests should go through core/net/http_client.py" -ForegroundColor Gray
    exit 0
}

Write-Host "[FAIL] Found $($Violations.Count) potential bypass(es):" -ForegroundColor Red
Write-Host ""

foreach ($v in $Violations) {
    Write-Host "  $($v.File):$($v.Line)" -ForegroundColor Yellow
    Write-Host "    Pattern: $($v.Pattern)" -ForegroundColor DarkGray
    Write-Host "    Content: $($v.Content)" -ForegroundColor White
    Write-Host ""
}

if ($FixSuggestions) {
    Write-Host "=" * 60 -ForegroundColor Cyan
    Write-Host "FIX SUGGESTIONS" -ForegroundColor Cyan
    Write-Host "=" * 60 -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Replace direct HTTP calls with:" -ForegroundColor White
    Write-Host ""
    Write-Host "  from core.net import http_get" -ForegroundColor Green
    Write-Host "  status, body, url = http_get('https://...')" -ForegroundColor Green
    Write-Host ""
    Write-Host "This ensures:" -ForegroundColor Gray
    Write-Host "  - AllowList.txt enforcement" -ForegroundColor Gray
    Write-Host "  - Audit logging" -ForegroundColor Gray
    Write-Host "  - Redirect safety" -ForegroundColor Gray
}

exit 1
