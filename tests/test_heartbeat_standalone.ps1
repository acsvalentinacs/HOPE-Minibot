# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T21:10:00Z
# Purpose: Standalone test for heartbeat check logic
# === END SIGNATURE ===

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ROOT = 'C:\Users\kirillDev\Desktop\TradingBot\minibot'
$TEMP_DIR = Join-Path $env:TEMP 'hope_hb_test'

# Copy of Test-HeartbeatFresh for isolated testing
function Test-HeartbeatFresh {
    param(
        [Parameter(Mandatory)]
        [string]$HealthFile,
        [int]$ThresholdSec = 60
    )

    $result = [PSCustomObject]@{
        IsFresh = $false
        AgeSec = -1
        Error = $null
        RawData = $null
    }

    if (-not (Test-Path $HealthFile)) {
        $result.Error = "FILE_NOT_FOUND"
        return $result
    }

    try {
        $content = Get-Content -Path $HealthFile -Raw -Encoding UTF8
        if ([string]::IsNullOrWhiteSpace($content)) {
            $result.Error = "EMPTY_FILE"
            return $result
        }
        $json = $content | ConvertFrom-Json
        $result.RawData = $json
    } catch {
        $result.Error = "JSON_PARSE_ERROR: $($_.Exception.Message)"
        return $result
    }

    $hbTs = $json.PSObject.Properties['hb_ts']
    if (-not $hbTs -or [string]::IsNullOrWhiteSpace($hbTs.Value)) {
        $result.Error = "MISSING_HB_TS"
        return $result
    }
    $hbTsValue = $hbTs.Value

    try {
        $hbTime = [DateTime]::Parse($hbTsValue).ToUniversalTime()
        $nowUtc = [DateTime]::UtcNow
        $ageSec = [int]($nowUtc - $hbTime).TotalSeconds
        if ($ageSec -lt 0) {
            $result.Error = "CLOCK_SKEW: age=${ageSec}s"
            return $result
        }
        $result.AgeSec = $ageSec
    } catch {
        $result.Error = "TIMESTAMP_PARSE_ERROR: $($_.Exception.Message)"
        return $result
    }

    if ($ageSec -le $ThresholdSec) {
        $result.IsFresh = $true
    } else {
        $result.Error = "STALE: age=${ageSec}s > threshold=${ThresholdSec}s"
    }

    return $result
}

# Setup
if (-not (Test-Path $TEMP_DIR)) {
    New-Item -ItemType Directory -Path $TEMP_DIR -Force | Out-Null
}

$passed = 0
$failed = 0

function Assert {
    param([string]$Name, [bool]$Condition)
    if ($Condition) {
        Write-Host "[PASS] $Name" -ForegroundColor Green
        $script:passed++
    } else {
        Write-Host "[FAIL] $Name" -ForegroundColor Red
        $script:failed++
    }
}

Write-Host "`n=== HEARTBEAT CHECK TESTS ===" -ForegroundColor Cyan

# Test 1: FILE_NOT_FOUND
$r = Test-HeartbeatFresh -HealthFile "C:\nonexistent\xyz123.json"
Assert "FILE_NOT_FOUND when missing" ($r.IsFresh -eq $false -and $r.Error -eq "FILE_NOT_FOUND")

# Test 2: Fresh heartbeat
$testFile = Join-Path $TEMP_DIR 'fresh.json'
$nowUtc = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
@{ hb_ts = $nowUtc } | ConvertTo-Json | Set-Content -Path $testFile -Encoding UTF8
$r = Test-HeartbeatFresh -HealthFile $testFile -ThresholdSec 60
Assert "IsFresh=true for recent" ($r.IsFresh -eq $true -and $r.AgeSec -le 5)

# Test 3: Stale heartbeat
$testFile = Join-Path $TEMP_DIR 'stale.json'
$oldTime = [DateTime]::UtcNow.AddSeconds(-120).ToString("yyyy-MM-ddTHH:mm:ssZ")
@{ hb_ts = $oldTime } | ConvertTo-Json | Set-Content -Path $testFile -Encoding UTF8
$r = Test-HeartbeatFresh -HealthFile $testFile -ThresholdSec 60
Assert "IsFresh=false for stale (120s)" ($r.IsFresh -eq $false -and $r.AgeSec -gt 100)

# Test 4: Invalid JSON
$testFile = Join-Path $TEMP_DIR 'invalid.json'
"not valid json {{{{" | Set-Content -Path $testFile -Encoding UTF8
$r = Test-HeartbeatFresh -HealthFile $testFile
Assert "JSON_PARSE_ERROR for invalid" ($r.IsFresh -eq $false -and $r.Error -like "JSON_PARSE_ERROR*")

# Test 5: Missing hb_ts
$testFile = Join-Path $TEMP_DIR 'no_hbts.json'
@{ other = "data" } | ConvertTo-Json | Set-Content -Path $testFile -Encoding UTF8
$r = Test-HeartbeatFresh -HealthFile $testFile
Assert "MISSING_HB_TS when absent" ($r.IsFresh -eq $false -and $r.Error -eq "MISSING_HB_TS")

# Test 6: Empty file
$testFile = Join-Path $TEMP_DIR 'empty.json'
[System.IO.File]::WriteAllText($testFile, "")
$r = Test-HeartbeatFresh -HealthFile $testFile
Assert "EMPTY_FILE for empty" ($r.IsFresh -eq $false -and $r.Error -eq "EMPTY_FILE")

# Test 7: Real health_tgbot.json (if exists)
$realFile = Join-Path $ROOT 'state\health_tgbot.json'
if (Test-Path $realFile) {
    $r = Test-HeartbeatFresh -HealthFile $realFile -ThresholdSec 3600
    Assert "Parse real health_tgbot.json" ($r.RawData -ne $null)
} else {
    Write-Host "[SKIP] health_tgbot.json not found" -ForegroundColor Yellow
}

# Cleanup
Remove-Item -Path $TEMP_DIR -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "`n=== RESULTS ===" -ForegroundColor Cyan
Write-Host "Passed: $passed" -ForegroundColor Green
Write-Host "Failed: $failed" -ForegroundColor $(if ($failed -gt 0) { 'Red' } else { 'Green' })

exit $(if ($failed -gt 0) { 1 } else { 0 })
