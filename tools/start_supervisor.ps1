# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-01-30T02:20:00Z
# Purpose: Start HOPE AI Supervisor as background daemon
# === END SIGNATURE ===
<#
.SYNOPSIS
    Start HOPE AI Supervisor as background daemon.

.DESCRIPTION
    Launches the AI Supervisor which monitors the Production Engine
    and automatically restarts it if it crashes.

.PARAMETER Mode
    Trading mode: DRY, TESTNET, LIVE

.PARAMETER Action
    Action to perform: start, stop, status, restart

.EXAMPLE
    .\start_supervisor.ps1 -Action start -Mode TESTNET
    .\start_supervisor.ps1 -Action status
    .\start_supervisor.ps1 -Action stop
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

$VENV_PYTHON = 'C:\Users\kirillDev\Desktop\TradingBot\.venv\Scripts\python.exe'
$SYS_PYTHON = 'python'
$PY = if (Test-Path $VENV_PYTHON) { $VENV_PYTHON } else { $SYS_PYTHON }

$SUPERVISOR_SCRIPT = Join-Path $ROOT 'scripts\hope_supervisor.py'
$SUPERVISOR_LOCK = Join-Path $ROOT 'state\locks\supervisor.lock'
$STOP_FLAG = Join-Path $ROOT 'state\STOP.flag'
$LOG_FILE = Join-Path $ROOT 'logs\supervisor.log'

# Ensure directories
New-Item -ItemType Directory -Path (Join-Path $ROOT 'state\locks') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $ROOT 'logs') -Force | Out-Null

# === FUNCTIONS ===

function Get-SupervisorStatus {
    if (Test-Path $SUPERVISOR_LOCK) {
        $lockPid = Get-Content $SUPERVISOR_LOCK -ErrorAction SilentlyContinue
        if ($lockPid) {
            $proc = Get-Process -Id $lockPid -ErrorAction SilentlyContinue
            if ($proc -and -not $proc.HasExited) {
                return @{ Running = $true; PID = $lockPid }
            }
        }
    }
    return @{ Running = $false; PID = $null }
}

function Start-Supervisor {
    param([string]$Mode)

    $status = Get-SupervisorStatus
    if ($status.Running) {
        Write-Host "[WARN] Supervisor already running (PID: $($status.PID))" -ForegroundColor Yellow
        return
    }

    # Remove STOP flag
    if (Test-Path $STOP_FLAG) {
        Remove-Item $STOP_FLAG -Force
    }

    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Starting AI Supervisor in $Mode mode..." -ForegroundColor Cyan

    # Start supervisor in background
    $proc = Start-Process -FilePath $PY `
        -ArgumentList @('-u', $SUPERVISOR_SCRIPT, '--mode', $Mode) `
        -WorkingDirectory $ROOT `
        -PassThru `
        -NoNewWindow `
        -RedirectStandardOutput $LOG_FILE `
        -RedirectStandardError "$LOG_FILE.err"

    Start-Sleep -Seconds 2

    # Verify started
    $status = Get-SupervisorStatus
    if ($status.Running) {
        Write-Host "[OK] Supervisor started (PID: $($status.PID))" -ForegroundColor Green
    } else {
        Write-Host "[ERROR] Failed to start supervisor" -ForegroundColor Red
        if (Test-Path "$LOG_FILE.err") {
            Get-Content "$LOG_FILE.err" -Tail 10
        }
    }
}

function Stop-Supervisor {
    $status = Get-SupervisorStatus
    if (-not $status.Running) {
        Write-Host "[INFO] Supervisor not running" -ForegroundColor Yellow
        return
    }

    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Stopping AI Supervisor (PID: $($status.PID))..." -ForegroundColor Yellow

    # Set STOP flag
    "STOPPED at $(Get-Date -Format 'yyyy-MM-ddTHH:mm:ssZ')" | Out-File $STOP_FLAG -Encoding UTF8

    # Wait for graceful shutdown
    for ($i = 0; $i -lt 30; $i++) {
        $status = Get-SupervisorStatus
        if (-not $status.Running) {
            Write-Host "[OK] Supervisor stopped" -ForegroundColor Green
            return
        }
        Start-Sleep -Seconds 1
    }

    # Force kill
    if ($status.Running) {
        Stop-Process -Id $status.PID -Force -ErrorAction SilentlyContinue
        Write-Host "[OK] Supervisor force-stopped" -ForegroundColor Yellow
    }
}

function Show-Status {
    Write-Host "`nHOPE AI Supervisor Status:" -ForegroundColor Cyan
    Write-Host "==========================" -ForegroundColor Cyan

    $supStatus = Get-SupervisorStatus

    if ($supStatus.Running) {
        Write-Host "  Supervisor: RUNNING (PID: $($supStatus.PID))" -ForegroundColor Green

        # Get detailed status from Python
        $result = & $PY $SUPERVISOR_SCRIPT --status 2>&1
        if ($LASTEXITCODE -eq 0) {
            $json = $result | ConvertFrom-Json
            Write-Host "  Engine:     $($json.engine.status)" -ForegroundColor $(if ($json.engine.is_alive) { 'Green' } else { 'Red' })
            Write-Host "  Mode:       $($json.supervisor.mode)"
            Write-Host "  Restarts:   $($json.stats.total_restarts)"
            if ($json.engine.heartbeat_age) {
                Write-Host "  Heartbeat:  $([math]::Round($json.engine.heartbeat_age))s ago"
            }
        }
    } else {
        Write-Host "  Supervisor: NOT RUNNING" -ForegroundColor Red
    }

    # Check engine directly
    $engineLock = Join-Path $ROOT 'state\locks\production_engine.lock'
    if (Test-Path $engineLock) {
        $enginePid = Get-Content $engineLock -ErrorAction SilentlyContinue
        $proc = Get-Process -Id $enginePid -ErrorAction SilentlyContinue
        if ($proc -and -not $proc.HasExited) {
            Write-Host "  Engine PID: $enginePid" -ForegroundColor Green
        }
    }

    Write-Host ""
}

# === MAIN ===
switch ($Action) {
    'start' {
        Start-Supervisor -Mode $Mode
    }
    'stop' {
        Stop-Supervisor
    }
    'restart' {
        Stop-Supervisor
        Start-Sleep -Seconds 3
        Start-Supervisor -Mode $Mode
    }
    'status' {
        Show-Status
    }
}
