# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-01-30T01:50:00Z
# Purpose: HOPE Stack Launcher v2 - Telegram Bot Compatible
# Contract: fail-closed, PID tracking, health reporting
# === END SIGNATURE ===
<#
.SYNOPSIS
    HOPE Stack Launcher v2 - Called by Telegram Bot for stack management.

.DESCRIPTION
    Lightweight launcher that:
    - Starts/stops HOPE stack components
    - Reports PIDs and status
    - Works with Telegram bot commands (/morning, /night, /stack)

.PARAMETER Action
    Action to perform: start, stop, status, restart

.PARAMETER Mode
    Trading mode: DRY, TESTNET, LIVE

.EXAMPLE
    .\launch_hope_stack_pidtruth_v2.ps1 -Action start -Mode TESTNET
    .\launch_hope_stack_pidtruth_v2.ps1 -Action status
#>

param(
    [ValidateSet('start', 'stop', 'status', 'restart')]
    [string]$Action = 'status',

    [ValidateSet('DRY', 'TESTNET', 'LIVE')]
    [string]$Mode = 'TESTNET'
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# === CONFIGURATION ===
$ROOT = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path (Join-Path $ROOT 'core'))) {
    $ROOT = 'C:\Users\kirillDev\Desktop\TradingBot\minibot'
}

$STATE_DIR = Join-Path $ROOT 'state'
$LOCKS_DIR = Join-Path $STATE_DIR 'locks'
$PID_FILE = Join-Path $LOCKS_DIR 'hope_stack.pid'
$STOP_FLAG = Join-Path $STATE_DIR 'STOP.flag'

$VENV_PYTHON = 'C:\Users\kirillDev\Desktop\TradingBot\.venv\Scripts\python.exe'
$SYS_PYTHON = 'python'
$PY = if (Test-Path $VENV_PYTHON) { $VENV_PYTHON } else { $SYS_PYTHON }

# Ensure directories
New-Item -ItemType Directory -Path $LOCKS_DIR -Force | Out-Null

# === FUNCTIONS ===

function Get-StackStatus {
    <#
    .SYNOPSIS
        Returns current stack status.
    #>
    $result = @{
        'ENGINE' = 'MISSING'
        'TGBOT' = 'MISSING'
        'LISTENER' = 'MISSING'
    }

    # Check if PID file exists
    if (Test-Path $PID_FILE) {
        $pids = Get-Content $PID_FILE -ErrorAction SilentlyContinue | ForEach-Object {
            $parts = $_ -split '='
            @{ Name = $parts[0]; PID = $parts[1] }
        }

        foreach ($p in $pids) {
            $proc = Get-Process -Id $p.PID -ErrorAction SilentlyContinue
            if ($proc -and -not $proc.HasExited) {
                $result[$p.Name] = "RUNNING (PID: $($p.PID))"
            }
        }
    }

    # Check production engine via lock file
    $engineLock = Join-Path $LOCKS_DIR 'production_engine.lock'
    if (Test-Path $engineLock) {
        $pid = Get-Content $engineLock -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($pid) {
            $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
            if ($proc -and -not $proc.HasExited) {
                $result['ENGINE'] = "RUNNING (PID: $pid)"
            }
        }
    }

    # Check TgBot process
    $tgBotProcs = Get-Process -Name 'python*' -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'tg_bot_simple' }
    if ($tgBotProcs) {
        $result['TGBOT'] = "RUNNING (PID: $($tgBotProcs[0].Id))"
    }

    return $result
}

function Start-Stack {
    param([string]$Mode)

    Write-Host "[$(Get-Date -Format 'yyyy-MM-ddTHH:mm:ssZ')] Starting HOPE Stack in $Mode mode..." -ForegroundColor Cyan

    # Remove STOP flag
    if (Test-Path $STOP_FLAG) {
        Remove-Item $STOP_FLAG -Force
        Write-Host "[OK] STOP.flag removed" -ForegroundColor Green
    }

    # Start production engine
    $engineScript = Join-Path $ROOT 'scripts\start_hope_production.py'
    if (Test-Path $engineScript) {
        $engineProc = Start-Process -FilePath $PY `
            -ArgumentList @($engineScript, '--mode', $Mode, '--position-size', '15') `
            -WorkingDirectory $ROOT `
            -PassThru `
            -NoNewWindow

        "ENGINE=$($engineProc.Id)" | Out-File $PID_FILE -Encoding UTF8
        Write-Host "[STARTED] Production Engine (PID: $($engineProc.Id))" -ForegroundColor Green
    } else {
        # Fallback to core entrypoint
        $coreProc = Start-Process -FilePath $PY `
            -ArgumentList @('-m', 'core.entrypoint', '--mode', $Mode) `
            -WorkingDirectory $ROOT `
            -PassThru `
            -NoNewWindow

        "ENGINE=$($coreProc.Id)" | Out-File $PID_FILE -Encoding UTF8
        Write-Host "[STARTED] Core Engine (PID: $($coreProc.Id))" -ForegroundColor Green
    }

    # Start TgBot if not already running
    $tgBotPath = Join-Path $ROOT 'tg_bot_simple.py'
    $existingTgBot = Get-Process -Name 'python*' -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'tg_bot_simple' }

    if (-not $existingTgBot -and (Test-Path $tgBotPath)) {
        Start-Sleep -Seconds 2
        $tgBotProc = Start-Process -FilePath $PY `
            -ArgumentList @('-u', $tgBotPath) `
            -WorkingDirectory $ROOT `
            -PassThru `
            -NoNewWindow

        "TGBOT=$($tgBotProc.Id)" | Add-Content $PID_FILE -Encoding UTF8
        Write-Host "[STARTED] TgBot (PID: $($tgBotProc.Id))" -ForegroundColor Green
    }

    Write-Host "[COMPLETE] Stack started" -ForegroundColor Green
}

function Stop-Stack {
    Write-Host "[$(Get-Date -Format 'yyyy-MM-ddTHH:mm:ssZ')] Stopping HOPE Stack..." -ForegroundColor Yellow

    # Set STOP flag
    "STOPPED at $(Get-Date -Format 'yyyy-MM-ddTHH:mm:ssZ')" | Out-File $STOP_FLAG -Encoding UTF8
    Write-Host "[OK] STOP.flag set" -ForegroundColor Green

    # Kill processes from PID file
    if (Test-Path $PID_FILE) {
        Get-Content $PID_FILE | ForEach-Object {
            $parts = $_ -split '='
            $pid = $parts[1]
            $name = $parts[0]

            $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
            if ($proc -and -not $proc.HasExited) {
                Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
                Write-Host "[STOPPED] $name (PID: $pid)" -ForegroundColor Green
            }
        }
        Remove-Item $PID_FILE -Force
    }

    # Kill production engine lock
    $engineLock = Join-Path $LOCKS_DIR 'production_engine.lock'
    if (Test-Path $engineLock) {
        $pid = Get-Content $engineLock -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($pid) {
            Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
            Write-Host "[STOPPED] Production Engine (PID: $pid)" -ForegroundColor Green
        }
        Remove-Item $engineLock -Force -ErrorAction SilentlyContinue
    }

    Write-Host "[COMPLETE] Stack stopped" -ForegroundColor Green
}

# === MAIN ===
switch ($Action) {
    'start' {
        Start-Stack -Mode $Mode
    }
    'stop' {
        Stop-Stack
    }
    'restart' {
        Stop-Stack
        Start-Sleep -Seconds 3
        Start-Stack -Mode $Mode
    }
    'status' {
        Write-Host "`nHOPE Stack Status:" -ForegroundColor Cyan
        Write-Host "==================" -ForegroundColor Cyan

        $status = Get-StackStatus
        foreach ($key in $status.Keys) {
            $value = $status[$key]
            $color = if ($value -match 'RUNNING') { 'Green' } else { 'Red' }
            Write-Host "  $($key.PadRight(10)): $value" -ForegroundColor $color
        }

        # Check STOP flag
        $stopFlagStatus = if (Test-Path $STOP_FLAG) { 'ON' } else { 'OFF' }
        Write-Host "  STOP.flag : $stopFlagStatus" -ForegroundColor $(if ($stopFlagStatus -eq 'ON') { 'Yellow' } else { 'Green' })

        Write-Host ""
    }
}
