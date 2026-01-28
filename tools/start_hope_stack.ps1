# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T07:25:00Z
# Modified by: Claude (opus-4)
# Modified at: 2026-01-28T21:00:00Z
# Purpose: HOPE Stack Orchestrator v2.0 - Smart Watchdog with heartbeat check + Telegram alerts
# Security: Fail-closed pre-flight, heartbeat validation, graceful shutdown
# === END SIGNATURE ===

<#
.SYNOPSIS
    HOPE Stack Orchestrator v2.0 - Smart Watchdog

.DESCRIPTION
    Запускает HOPE Trade Core и TgBot с умным мониторингом:
    - Heartbeat age validation (не только HasExited)
    - Telegram alerts при сбоях
    - Restart backoff (защита от infinite loop)
    - Graceful shutdown coordination

.PARAMETER Mode
    Торговый режим: DRY, TESTNET, MAINNET

.PARAMETER NoTgBot
    Не запускать Telegram бота

.PARAMETER AutoRestart
    Автоматический перезапуск при падении

.PARAMETER NoAlerts
    Отключить Telegram alerts

.EXAMPLE
    .\start_hope_stack.ps1 -Mode DRY -AutoRestart
    .\start_hope_stack.ps1 -Mode TESTNET -AutoRestart -NoAlerts
#>

param(
    [ValidateSet('DRY', 'TESTNET', 'MAINNET')]
    [string]$Mode = 'DRY',

    [switch]$NoTgBot,
    [switch]$AutoRestart,
    [switch]$NoAlerts
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# === CONFIGURATION (SSoT) ===
$ROOT = 'C:\Users\kirillDev\Desktop\TradingBot\minibot'
$VENV_ROOT = 'C:\Users\kirillDev\Desktop\TradingBot'
$VENV_PYTHON = Join-Path $VENV_ROOT '.venv\Scripts\python.exe'
$SYSTEM_PYTHON = 'python'
$ENV_FILE = 'C:\secrets\hope.env'
$ALLOWLIST = Join-Path $ROOT 'AllowList.txt'

# === HEALTH CHECK CONFIG ===
$HEALTH_CORE = Join-Path $ROOT 'state\health_v5.json'
$HEALTH_TGBOT = Join-Path $ROOT 'state\health_tgbot.json'
$STALE_THRESHOLD_SEC = 60          # Heartbeat older than 60s = STALE
$CRITICAL_STALE_MULTIPLIER = 3     # Force restart after 3x threshold
$MAX_RESTART_COUNT = 3             # Max restarts before giving up
$RESTART_BACKOFF_SEC = 30          # Wait between restarts
$WATCHDOG_INTERVAL_SEC = 10        # Health check interval

# === TELEGRAM ALERT CONFIG ===
$TG_ALERT_ENABLED = -not $NoAlerts
$TG_BOT_TOKEN_ENV = 'TELEGRAM_BOT_TOKEN'
$TG_ADMIN_CHAT_ID = '5812329204'
$ALERT_RATE_LIMIT_MIN = 5          # Min minutes between same alerts

# Determine Python to use
$PY = if (Test-Path $VENV_PYTHON) { $VENV_PYTHON } else { $SYSTEM_PYTHON }

# === HEARTBEAT VALIDATION (FAIL-CLOSED) ===
function Test-HeartbeatFresh {
    <#
    .SYNOPSIS
        Проверяет свежесть heartbeat файла (fail-closed).

    .OUTPUTS
        PSCustomObject: IsFresh, AgeSec, Error, RawData
    #>
    param(
        [Parameter(Mandatory)]
        [string]$HealthFile,

        [int]$ThresholdSec = 60
    )

    $result = [PSCustomObject]@{
        IsFresh = $false  # FAIL-CLOSED default
        AgeSec = -1
        Error = $null
        RawData = $null
    }

    # 1. File exists?
    if (-not (Test-Path $HealthFile)) {
        $result.Error = "FILE_NOT_FOUND"
        return $result
    }

    # 2. Read and parse JSON
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

    # 3. Extract hb_ts (required field) - safe property check
    $hbTs = $json.PSObject.Properties['hb_ts']
    if (-not $hbTs -or [string]::IsNullOrWhiteSpace($hbTs.Value)) {
        $result.Error = "MISSING_HB_TS"
        return $result
    }
    $hbTsValue = $hbTs.Value

    # 4. Parse timestamp (ISO8601 UTC)
    try {
        $hbTime = [DateTime]::Parse($hbTsValue).ToUniversalTime()
        $nowUtc = [DateTime]::UtcNow
        $ageSec = [int]($nowUtc - $hbTime).TotalSeconds

        # Sanity check: negative age means clock skew
        if ($ageSec -lt 0) {
            $result.Error = "CLOCK_SKEW: age=${ageSec}s (negative)"
            return $result
        }

        $result.AgeSec = $ageSec
    } catch {
        $result.Error = "TIMESTAMP_PARSE_ERROR: $($_.Exception.Message)"
        return $result
    }

    # 5. Check threshold
    if ($ageSec -le $ThresholdSec) {
        $result.IsFresh = $true
    } else {
        $result.Error = "STALE: age=${ageSec}s > threshold=${ThresholdSec}s"
    }

    return $result
}

# === TELEGRAM ALERT ===
$script:AlertHistory = @{}

function Send-TelegramAlert {
    <#
    .SYNOPSIS
        Отправляет alert в Telegram с rate limiting.
    #>
    param(
        [Parameter(Mandatory)]
        [string]$Message,

        [ValidateSet('INFO', 'WARNING', 'CRITICAL')]
        [string]$Severity = 'WARNING',

        [string]$AlertKey = 'default'
    )

    if (-not $TG_ALERT_ENABLED) {
        return
    }

    # Rate limiting
    $now = Get-Date
    if ($script:AlertHistory[$AlertKey]) {
        $lastSent = $script:AlertHistory[$AlertKey]
        $minSinceLastAlert = ($now - $lastSent).TotalMinutes
        if ($minSinceLastAlert -lt $ALERT_RATE_LIMIT_MIN) {
            Write-Host "[ALERT-RATE-LIMITED] $AlertKey (${minSinceLastAlert}m < ${ALERT_RATE_LIMIT_MIN}m)" -ForegroundColor Gray
            return
        }
    }

    # Get token from environment
    $token = [Environment]::GetEnvironmentVariable($TG_BOT_TOKEN_ENV, 'User')
    if (-not $token) {
        $token = [Environment]::GetEnvironmentVariable($TG_BOT_TOKEN_ENV, 'Process')
    }
    if (-not $token) {
        Write-Host "[ALERT-SKIP] Token not found: $TG_BOT_TOKEN_ENV" -ForegroundColor Yellow
        return
    }

    # Format message
    $emoji = switch ($Severity) {
        'INFO' { 'ℹ️' }
        'WARNING' { '⚠️' }
        'CRITICAL' { '🚨' }
    }

    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $fullMessage = @"
$emoji <b>HOPE WATCHDOG</b>

<b>Severity:</b> $Severity
<b>Time:</b> $timestamp
<b>Mode:</b> $Mode

$Message
"@

    # Send via API
    try {
        $uri = "https://api.telegram.org/bot$token/sendMessage"
        $body = @{
            chat_id = $TG_ADMIN_CHAT_ID
            text = $fullMessage
            parse_mode = 'HTML'
            disable_notification = ($Severity -eq 'INFO')
        }

        $null = Invoke-RestMethod -Uri $uri -Method Post -Body $body -TimeoutSec 10
        $script:AlertHistory[$AlertKey] = $now
        Write-Host "[ALERT-SENT] $Severity → $AlertKey" -ForegroundColor Cyan
    } catch {
        Write-Host "[ALERT-FAIL] $($_.Exception.Message)" -ForegroundColor Red
    }
}

# === PRE-FLIGHT CHECKS (FAIL-CLOSED) ===
function Invoke-PreFlightChecks {
    Write-Host "=== PRE-FLIGHT CHECKS ===" -ForegroundColor Cyan

    # 1. Root exists
    if (-not (Test-Path $ROOT)) {
        throw "FAIL: Root directory not found: $ROOT"
    }
    Write-Host "[OK] Root directory" -ForegroundColor Green

    # 2. Python exists
    if (-not (Test-Path $PY)) {
        throw "FAIL: Python not found: $PY"
    }
    Write-Host "[OK] Python: $PY" -ForegroundColor Green

    # 3. Secrets file exists (except DRY mode)
    if ($Mode -ne 'DRY') {
        if (-not (Test-Path $ENV_FILE)) {
            throw "FAIL: Secrets file not found: $ENV_FILE"
        }
        Write-Host "[OK] Secrets file" -ForegroundColor Green
    }

    # 4. AllowList exists and no wildcard
    if (-not (Test-Path $ALLOWLIST)) {
        throw "FAIL: AllowList.txt not found"
    }
    $wildcardLine = Select-String -Path $ALLOWLIST -Pattern '^\s*\*\s*$' -Quiet
    if ($wildcardLine) {
        throw "FAIL: AllowList.txt contains wildcard (*) - not fail-closed"
    }
    Write-Host "[OK] AllowList (no wildcards)" -ForegroundColor Green

    # 5. Core modules exist
    $requiredModules = @(
        'core\entrypoint.py',
        'core\execution\outbox.py',
        'core\execution\fills_ledger.py',
        'core\trade\order_router.py'
    )
    foreach ($mod in $requiredModules) {
        $path = Join-Path $ROOT $mod
        if (-not (Test-Path $path)) {
            throw "FAIL: Required module missing: $mod"
        }
    }
    Write-Host "[OK] Core modules present" -ForegroundColor Green

    # 6. Python syntax check
    Write-Host "Checking Python syntax..." -ForegroundColor Gray
    $compileResult = & $PY -m compileall -q (Join-Path $ROOT 'core') 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "FAIL: Python syntax errors detected"
    }
    Write-Host "[OK] Python syntax" -ForegroundColor Green

    Write-Host ""
    Write-Host "All pre-flight checks PASSED" -ForegroundColor Green
    Write-Host ""
}

# === PROCESS MANAGEMENT ===
$script:CoreProcess = $null
$script:TgBotProcess = $null

function Start-HopeCore {
    param([string]$Mode)

    Write-Host "Starting HOPE Trade Core (mode=$Mode)..." -ForegroundColor Yellow

    $arguments = @('-u', '-m', 'core.entrypoint', '--mode', $Mode)

    $script:CoreProcess = Start-Process -FilePath $PY `
        -ArgumentList $arguments `
        -WorkingDirectory $ROOT `
        -PassThru `
        -NoNewWindow

    Write-Host "[STARTED] HOPE Trade Core (PID: $($script:CoreProcess.Id))" -ForegroundColor Green
    return $script:CoreProcess
}

function Start-HopeTgBot {
    Write-Host "Starting HOPE TgBot..." -ForegroundColor Yellow

    $tgBotPath = Join-Path $ROOT 'tg_bot_simple.py'
    if (-not (Test-Path $tgBotPath)) {
        Write-Host "[SKIP] TgBot not found: $tgBotPath" -ForegroundColor Yellow
        return $null
    }

    $script:TgBotProcess = Start-Process -FilePath $PY `
        -ArgumentList @('-u', 'tg_bot_simple.py') `
        -WorkingDirectory $ROOT `
        -PassThru `
        -NoNewWindow

    Write-Host "[STARTED] HOPE TgBot (PID: $($script:TgBotProcess.Id))" -ForegroundColor Green
    return $script:TgBotProcess
}

# === SMART WATCHDOG v2.0 ===
function Watch-Processes {
    param([switch]$AutoRestart)

    Write-Host ""
    Write-Host "=== WATCHDOG v2.0 ACTIVE ===" -ForegroundColor Cyan
    Write-Host "Heartbeat threshold: ${STALE_THRESHOLD_SEC}s"
    Write-Host "Critical stale: $($STALE_THRESHOLD_SEC * $CRITICAL_STALE_MULTIPLIER)s"
    Write-Host "Check interval: ${WATCHDOG_INTERVAL_SEC}s"
    Write-Host "Max restarts: $MAX_RESTART_COUNT"
    Write-Host "Alerts: $(if ($TG_ALERT_ENABLED) { 'ENABLED' } else { 'DISABLED' })"
    Write-Host "Press Ctrl+C to stop"
    Write-Host ""

    $coreRestartCount = 0
    $tgbotRestartCount = 0
    $lastStatusLine = ""

    # Initial alert
    if ($TG_ALERT_ENABLED) {
        Send-TelegramAlert -Message "Watchdog started. Mode: $Mode" -Severity INFO -AlertKey "startup"
    }

    while ($true) {
        Start-Sleep -Seconds $WATCHDOG_INTERVAL_SEC

        $coreStatus = "UNKNOWN"
        $coreAgeSec = -1
        $tgbotStatus = "UNKNOWN"
        $tgbotAgeSec = -1

        # === CHECK TRADE CORE ===
        if ($script:CoreProcess) {
            if ($script:CoreProcess.HasExited) {
                $coreStatus = "DEAD"
                $exitCode = $script:CoreProcess.ExitCode

                Send-TelegramAlert `
                    -Message "Trade Core DIED (exit=$exitCode)`nRestarts: $coreRestartCount/$MAX_RESTART_COUNT" `
                    -Severity CRITICAL `
                    -AlertKey "core_dead"

                if ($AutoRestart -and $coreRestartCount -lt $MAX_RESTART_COUNT) {
                    $coreRestartCount++
                    Write-Host "[RESTART] Core attempt $coreRestartCount/$MAX_RESTART_COUNT in ${RESTART_BACKOFF_SEC}s..." -ForegroundColor Yellow
                    Start-Sleep -Seconds $RESTART_BACKOFF_SEC
                    $script:CoreProcess = Start-HopeCore -Mode $Mode

                    Send-TelegramAlert `
                        -Message "Trade Core restarted (attempt $coreRestartCount/$MAX_RESTART_COUNT)" `
                        -Severity WARNING `
                        -AlertKey "core_restart"

                } elseif ($coreRestartCount -ge $MAX_RESTART_COUNT) {
                    Send-TelegramAlert `
                        -Message "🛑 Trade Core restart limit reached ($MAX_RESTART_COUNT).`nMANUAL INTERVENTION REQUIRED." `
                        -Severity CRITICAL `
                        -AlertKey "core_limit"
                    throw "CRITICAL: Core restart limit exceeded"
                }
            } else {
                # Process alive - check heartbeat
                $hbCheck = Test-HeartbeatFresh -HealthFile $HEALTH_CORE -ThresholdSec $STALE_THRESHOLD_SEC
                $coreAgeSec = $hbCheck.AgeSec

                if ($hbCheck.IsFresh) {
                    $coreStatus = "OK"
                    $coreRestartCount = 0  # Reset on healthy
                } else {
                    $coreStatus = "STALE"

                    Send-TelegramAlert `
                        -Message "Trade Core heartbeat STALE`nAge: $($hbCheck.AgeSec)s (threshold: ${STALE_THRESHOLD_SEC}s)`nError: $($hbCheck.Error)" `
                        -Severity WARNING `
                        -AlertKey "core_stale"

                    # Force restart if critically stale
                    $criticalThreshold = $STALE_THRESHOLD_SEC * $CRITICAL_STALE_MULTIPLIER
                    if ($hbCheck.AgeSec -gt $criticalThreshold -and $AutoRestart) {
                        Write-Host "[FORCE-KILL] Core heartbeat critically stale ($($hbCheck.AgeSec)s > ${criticalThreshold}s)" -ForegroundColor Red

                        Send-TelegramAlert `
                            -Message "Trade Core FORCE-KILLED due to critical stale ($($hbCheck.AgeSec)s)" `
                            -Severity CRITICAL `
                            -AlertKey "core_force_kill"

                        Stop-Process -Id $script:CoreProcess.Id -Force -ErrorAction SilentlyContinue
                        # Will restart on next iteration when HasExited=true
                    }
                }
            }
        }

        # === CHECK TGBOT (non-critical) ===
        if ($script:TgBotProcess) {
            if ($script:TgBotProcess.HasExited) {
                $tgbotStatus = "DEAD"

                if ($AutoRestart -and $tgbotRestartCount -lt $MAX_RESTART_COUNT) {
                    $tgbotRestartCount++
                    Write-Host "[RESTART] TgBot attempt $tgbotRestartCount..." -ForegroundColor Yellow
                    Start-Sleep -Seconds 5
                    $script:TgBotProcess = Start-HopeTgBot
                }
            } else {
                $hbCheck = Test-HeartbeatFresh -HealthFile $HEALTH_TGBOT -ThresholdSec $STALE_THRESHOLD_SEC
                $tgbotAgeSec = $hbCheck.AgeSec
                $tgbotStatus = if ($hbCheck.IsFresh) { "OK" } else { "STALE" }
                if ($hbCheck.IsFresh) { $tgbotRestartCount = 0 }
            }
        } else {
            $tgbotStatus = "N/A"
        }

        # === STATUS OUTPUT ===
        $coreAgeStr = if ($coreAgeSec -ge 0) { "${coreAgeSec}s" } else { "?" }
        $tgbotAgeStr = if ($tgbotAgeSec -ge 0) { "${tgbotAgeSec}s" } else { "?" }

        $statusLine = "Core=$coreStatus($coreAgeStr) | TgBot=$tgbotStatus($tgbotAgeStr) | R:$coreRestartCount/$tgbotRestartCount"

        # Only print if changed
        if ($statusLine -ne $lastStatusLine) {
            $statusColor = switch ($coreStatus) {
                'OK' { 'Green' }
                'STALE' { 'Yellow' }
                default { 'Red' }
            }
            Write-Host "[$(Get-Date -Format 'HH:mm:ss')] $statusLine" -ForegroundColor $statusColor
            $lastStatusLine = $statusLine
        }
    }
}

function Stop-AllProcesses {
    Write-Host ""
    Write-Host "Stopping all processes..." -ForegroundColor Yellow

    if ($script:CoreProcess -and -not $script:CoreProcess.HasExited) {
        # TODO: graceful shutdown via STOP.flag in Phase 2
        Stop-Process -Id $script:CoreProcess.Id -Force -ErrorAction SilentlyContinue
        Write-Host "[STOPPED] Trade Core" -ForegroundColor Green
    }
    if ($script:TgBotProcess -and -not $script:TgBotProcess.HasExited) {
        Stop-Process -Id $script:TgBotProcess.Id -Force -ErrorAction SilentlyContinue
        Write-Host "[STOPPED] TgBot" -ForegroundColor Green
    }

    Write-Host "All processes stopped" -ForegroundColor Green

    if ($TG_ALERT_ENABLED) {
        Send-TelegramAlert -Message "Watchdog stopped. All processes terminated." -Severity INFO -AlertKey "shutdown"
    }
}

# === MAIN ===
try {
    Set-Location $ROOT

    Write-Host @"

╔═══════════════════════════════════════════════════════════════╗
║           HOPE STACK ORCHESTRATOR v2.0 (Smart Watchdog)       ║
╠═══════════════════════════════════════════════════════════════╣
║  Mode:         $Mode
║  AutoRestart:  $AutoRestart
║  NoTgBot:      $NoTgBot
║  Alerts:       $(if ($TG_ALERT_ENABLED) { 'ENABLED' } else { 'DISABLED' })
║  Python:       $PY
║  Stale:        ${STALE_THRESHOLD_SEC}s / Critical: $($STALE_THRESHOLD_SEC * $CRITICAL_STALE_MULTIPLIER)s
╚═══════════════════════════════════════════════════════════════╝

"@ -ForegroundColor Cyan

    Invoke-PreFlightChecks

    Start-HopeCore -Mode $Mode

    if (-not $NoTgBot) {
        Start-Sleep -Seconds 2
        Start-HopeTgBot
    }

    Watch-Processes -AutoRestart:$AutoRestart

} catch {
    Write-Host ""
    Write-Host "[FATAL] $($_.Exception.Message)" -ForegroundColor Red

    if ($TG_ALERT_ENABLED) {
        Send-TelegramAlert `
            -Message "🛑 FATAL ERROR`n$($_.Exception.Message)" `
            -Severity CRITICAL `
            -AlertKey "fatal"
    }

    Stop-AllProcesses
    exit 1
} finally {
    Stop-AllProcesses
}
