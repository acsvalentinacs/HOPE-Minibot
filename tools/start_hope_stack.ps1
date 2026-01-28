# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-28T07:25:00Z
# Purpose: HOPE Stack Orchestrator - единая точка запуска торговой системы
# Security: Fail-closed pre-flight, watchdog, graceful shutdown
# === END SIGNATURE ===

<#
.SYNOPSIS
    HOPE Stack Orchestrator - единая точка запуска торговой системы

.DESCRIPTION
    Запускает HOPE Trade Core и HOPE Control Plane (TgBot) как изолированные процессы
    с мониторингом здоровья (Watchdog)

.PARAMETER Mode
    Торговый режим: DRY, TESTNET, MAINNET

.PARAMETER NoTgBot
    Не запускать Telegram бота

.PARAMETER AutoRestart
    Автоматический перезапуск при падении

.EXAMPLE
    .\start_hope_stack.ps1 -Mode TESTNET
#>

param(
    [ValidateSet('DRY', 'TESTNET', 'MAINNET')]
    [string]$Mode = 'DRY',

    [switch]$NoTgBot,
    [switch]$AutoRestart
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

# Determine Python to use
$PY = if (Test-Path $VENV_PYTHON) { $VENV_PYTHON } else { $SYSTEM_PYTHON }

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
        'core\trade\order_router_v2.py'
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

    $args = @('-m', 'core.entrypoint', '--mode', $Mode)

    $script:CoreProcess = Start-Process -FilePath $PY `
        -ArgumentList $args `
        -WorkingDirectory $ROOT `
        -PassThru `
        -NoNewWindow

    Write-Host "[STARTED] HOPE Trade Core (PID: $($script:CoreProcess.Id))" -ForegroundColor Green
    return $script:CoreProcess
}

function Start-HopeTgBot {
    Write-Host "Starting HOPE Control Plane (TgBot)..." -ForegroundColor Yellow

    $tgBotPath = Join-Path $ROOT 'tg_bot_simple.py'
    if (-not (Test-Path $tgBotPath)) {
        Write-Host "[SKIP] TgBot not found: $tgBotPath" -ForegroundColor Yellow
        return $null
    }

    $script:TgBotProcess = Start-Process -FilePath $PY `
        -ArgumentList @('tg_bot_simple.py') `
        -WorkingDirectory $ROOT `
        -PassThru `
        -NoNewWindow

    Write-Host "[STARTED] HOPE Control Plane (PID: $($script:TgBotProcess.Id))" -ForegroundColor Green
    return $script:TgBotProcess
}

function Watch-Processes {
    param([switch]$AutoRestart)

    Write-Host ""
    Write-Host "=== WATCHDOG ACTIVE ===" -ForegroundColor Cyan
    Write-Host "Press Ctrl+C to stop all processes"
    Write-Host ""

    $lastStatus = ""
    while ($true) {
        Start-Sleep -Seconds 5

        # Check Core
        if ($script:CoreProcess -and $script:CoreProcess.HasExited) {
            $exitCode = $script:CoreProcess.ExitCode
            Write-Host ""
            Write-Host "[ALERT] HOPE Trade Core DIED (exit=$exitCode)" -ForegroundColor Red

            if ($AutoRestart -and $exitCode -ne 0) {
                Write-Host "Auto-restarting Core in 5 seconds..." -ForegroundColor Yellow
                Start-Sleep -Seconds 5
                $script:CoreProcess = Start-HopeCore -Mode $Mode
            } else {
                throw "CRITICAL: Trade Core died, manual intervention required"
            }
        }

        # Check TgBot (non-critical)
        if ($script:TgBotProcess -and $script:TgBotProcess.HasExited) {
            Write-Host "[WARNING] TgBot died (non-critical, trading continues)" -ForegroundColor Yellow
            if ($AutoRestart) {
                $script:TgBotProcess = Start-HopeTgBot
            }
        }

        # Status output
        $coreStatus = if ($script:CoreProcess -and -not $script:CoreProcess.HasExited) { "ALIVE" } else { "DEAD" }
        $tgStatus = if ($script:TgBotProcess -and -not $script:TgBotProcess.HasExited) { "ALIVE" } else { "N/A" }
        $status = "Core=$coreStatus | TgBot=$tgStatus"

        if ($status -ne $lastStatus) {
            Write-Host "[$(Get-Date -Format 'HH:mm:ss')] $status" -ForegroundColor Gray
            $lastStatus = $status
        }
    }
}

function Stop-AllProcesses {
    Write-Host ""
    Write-Host "Stopping all processes..." -ForegroundColor Yellow

    if ($script:CoreProcess -and -not $script:CoreProcess.HasExited) {
        Stop-Process -Id $script:CoreProcess.Id -Force -ErrorAction SilentlyContinue
        Write-Host "[STOPPED] Trade Core" -ForegroundColor Green
    }
    if ($script:TgBotProcess -and -not $script:TgBotProcess.HasExited) {
        Stop-Process -Id $script:TgBotProcess.Id -Force -ErrorAction SilentlyContinue
        Write-Host "[STOPPED] TgBot" -ForegroundColor Green
    }

    Write-Host "All processes stopped" -ForegroundColor Green
}

# === MAIN ===
try {
    Set-Location $ROOT

    Write-Host @"

╔═══════════════════════════════════════════════════════╗
║           HOPE STACK ORCHESTRATOR v1.0                ║
╠═══════════════════════════════════════════════════════╣
║  Mode:        $Mode
║  AutoRestart: $AutoRestart
║  NoTgBot:     $NoTgBot
║  Python:      $PY
╚═══════════════════════════════════════════════════════╝

"@ -ForegroundColor Cyan

    Invoke-PreFlightChecks

    Start-HopeCore -Mode $Mode

    if (-not $NoTgBot) {
        Start-Sleep -Seconds 2  # Give Core time to initialize
        Start-HopeTgBot
    }

    Watch-Processes -AutoRestart:$AutoRestart

} catch {
    Write-Host ""
    Write-Host "[FATAL] $($_.Exception.Message)" -ForegroundColor Red
    Stop-AllProcesses
    exit 1
} finally {
    Stop-AllProcesses
}
