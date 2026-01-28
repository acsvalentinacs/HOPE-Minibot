# Quick heartbeat check
$ROOT = "C:\Users\kirillDev\Desktop\TradingBot\minibot"

function Test-HeartbeatFresh {
    param([string]$HealthFile, [int]$ThresholdSec = 60)
    $result = [PSCustomObject]@{ IsFresh = $false; AgeSec = -1; Error = $null }
    if (-not (Test-Path $HealthFile)) { $result.Error = "FILE_NOT_FOUND"; return $result }
    try {
        $json = Get-Content -Path $HealthFile -Raw | ConvertFrom-Json
        $hbTs = $json.PSObject.Properties["hb_ts"]
        if (-not $hbTs) { $result.Error = "MISSING_HB_TS"; return $result }
        $hbTime = [DateTime]::Parse($hbTs.Value).ToUniversalTime()
        $ageSec = [int]([DateTime]::UtcNow - $hbTime).TotalSeconds
        $result.AgeSec = $ageSec
        $result.IsFresh = ($ageSec -le $ThresholdSec)
        if (-not $result.IsFresh) { $result.Error = "STALE: ${ageSec}s > ${ThresholdSec}s" }
    } catch { $result.Error = $_.Exception.Message }
    return $result
}

Write-Host "=== HEARTBEAT CHECK ===" -ForegroundColor Cyan
Write-Host "Time: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Gray

$core = Test-HeartbeatFresh -HealthFile "$ROOT\state\health_v5.json" -ThresholdSec 60
$coreColor = if ($core.IsFresh) {"Green"} else {"Red"}
Write-Host "Trade Core: IsFresh=$($core.IsFresh), Age=$($core.AgeSec)s" -ForegroundColor $coreColor
if ($core.Error) { Write-Host "  Error: $($core.Error)" -ForegroundColor Yellow }

$tgbot = Test-HeartbeatFresh -HealthFile "$ROOT\state\health_tgbot.json" -ThresholdSec 60
$tgColor = if ($tgbot.IsFresh) {"Green"} else {"Yellow"}
Write-Host "TgBot:      IsFresh=$($tgbot.IsFresh), Age=$($tgbot.AgeSec)s" -ForegroundColor $tgColor
if ($tgbot.Error) { Write-Host "  Error: $($tgbot.Error)" -ForegroundColor Yellow }

Write-Host ""
Write-Host "Processes:" -ForegroundColor Cyan
Get-Process python -ErrorAction SilentlyContinue | Select-Object Id, ProcessName, StartTime | Format-Table
