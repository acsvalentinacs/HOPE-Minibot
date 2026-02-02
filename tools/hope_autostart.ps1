# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-02-02 15:35:00 UTC
# Purpose: HOPE Auto-Start System with Multi-Layer Control
# === END SIGNATURE ===
<#
.SYNOPSIS
    HOPE Trading System Auto-Start with Multi-Layer Control

.DESCRIPTION
    Starts all HOPE components with multiple control layers:
    1. Process existence check (avoid duplicates)
    2. Port availability check
    3. Health verification after start
    4. Logging to state/startup/

.EXAMPLE
    .\tools\hope_autostart.ps1
    .\tools\hope_autostart.ps1 -SkipMomentum
    .\tools\hope_autostart.ps1 -DryRun
#>

param(
    [switch]$SkipMomentum,
    [switch]$DryRun,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

# === CONFIGURATION ===
$PROJECT_ROOT = "C:\Users\kirillDev\Desktop\TradingBot\minibot"
$LOG_DIR = "$PROJECT_ROOT\state\startup"
$SECRETS_PATH = "C:\secrets\hope.env"

# Components to start (order matters!)
$COMPONENTS = @(
    @{
        Name = "pricefeed_gateway"
        Script = "scripts/pricefeed_gateway.py"
        Port = 8100
        Required = $true
        WaitSec = 3
    },
    @{
        Name = "autotrader"
        Script = "scripts/autotrader.py"
        Args = @("--mode", "LIVE", "--yes", "--confirm")
        Port = 8200
        Required = $true
        WaitSec = 5
    },
    @{
        Name = "momentum_trader"
        Script = "scripts/momentum_trader.py"
        Args = @("--daemon")
        Port = $null  # No port, runs as daemon
        Required = $false
        WaitSec = 2
        ProcessCheck = "momentum_trader"
    },
    @{
        Name = "health_daemon"
        Script = "scripts/hope_health_daemon.py"
        Args = @("--interval", "60")
        Port = $null
        Required = $false
        WaitSec = 2
        ProcessCheck = "hope_health_daemon"
    }
)

# === FUNCTIONS ===

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $logLine = "$ts | $Level | $Message"
    Write-Host $logLine

    # Also write to log file
    if (-not (Test-Path $LOG_DIR)) {
        New-Item -ItemType Directory -Path $LOG_DIR -Force | Out-Null
    }
    $logFile = "$LOG_DIR\startup_$(Get-Date -Format 'yyyyMMdd').log"
    Add-Content -Path $logFile -Value $logLine
}

function Test-PortInUse {
    param([int]$Port)
    $result = netstat -ano | Select-String ":$Port.*LISTENING"
    return $null -ne $result
}

function Test-ProcessRunning {
    param([string]$ProcessName)
    $procs = Get-Process python* -ErrorAction SilentlyContinue
    foreach ($proc in $procs) {
        try {
            $cmdLine = (Get-CimInstance Win32_Process -Filter "ProcessId = $($proc.Id)").CommandLine
            if ($cmdLine -like "*$ProcessName*") {
                return $true
            }
        } catch {}
    }
    return $false
}

function Start-Component {
    param(
        [hashtable]$Component
    )

    $name = $Component.Name
    Write-Log "Starting $name..." "INFO"

    # Check 1: Already running by port?
    if ($Component.Port) {
        if (Test-PortInUse $Component.Port) {
            if (-not $Force) {
                Write-Log "$name already running on port $($Component.Port)" "WARN"
                return $true
            }
            Write-Log "Force mode: stopping existing $name" "WARN"
            # Find and kill process on port
            $pid = (netstat -ano | Select-String ":$($Component.Port).*LISTENING" |
                    ForEach-Object { $_.ToString().Trim().Split()[-1] } | Select-Object -First 1)
            if ($pid) {
                Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
                Start-Sleep -Seconds 1
            }
        }
    }

    # Check 2: Already running by process name?
    if ($Component.ProcessCheck) {
        if (Test-ProcessRunning $Component.ProcessCheck) {
            if (-not $Force) {
                Write-Log "$name already running (process check)" "WARN"
                return $true
            }
        }
    }

    # Start the component
    $scriptPath = Join-Path $PROJECT_ROOT $Component.Script
    if (-not (Test-Path $scriptPath)) {
        Write-Log "Script not found: $scriptPath" "ERROR"
        return $false
    }

    $processArgs = @($scriptPath) + ($Component.Args ?? @())

    if ($DryRun) {
        Write-Log "[DRY-RUN] Would start: python $($processArgs -join ' ')" "INFO"
        return $true
    }

    try {
        $proc = Start-Process python -ArgumentList $processArgs -WorkingDirectory $PROJECT_ROOT `
            -PassThru -WindowStyle Minimized

        Write-Log "$name started (PID: $($proc.Id))" "INFO"

        # Wait and verify
        Start-Sleep -Seconds $Component.WaitSec

        # Verify by port
        if ($Component.Port) {
            if (Test-PortInUse $Component.Port) {
                Write-Log "$name verified on port $($Component.Port)" "INFO"
                return $true
            } else {
                Write-Log "$name failed to bind port $($Component.Port)" "ERROR"
                return $false
            }
        }

        # Verify by process
        if ($Component.ProcessCheck) {
            if (Test-ProcessRunning $Component.ProcessCheck) {
                Write-Log "$name verified (process running)" "INFO"
                return $true
            }
        }

        return $true
    }
    catch {
        Write-Log "Failed to start $name : $_" "ERROR"
        return $false
    }
}

function Test-SystemHealth {
    Write-Log "Running health check..." "INFO"

    try {
        $status = Invoke-RestMethod -Uri "http://127.0.0.1:8200/status" -TimeoutSec 5
        Write-Log "AutoTrader: mode=$($status.mode), running=$($status.running), positions=$($status.open_positions)" "INFO"
        return $status.running
    }
    catch {
        Write-Log "Health check failed: $_" "ERROR"
        return $false
    }
}

# === MAIN ===

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "    HOPE TRADING SYSTEM AUTO-START" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

Write-Log "Starting HOPE Trading System..."
Write-Log "Project: $PROJECT_ROOT"

# Check secrets
if (-not (Test-Path $SECRETS_PATH)) {
    Write-Log "Secrets file not found: $SECRETS_PATH" "ERROR"
    exit 1
}

# Start components
$failed = @()
foreach ($comp in $COMPONENTS) {
    if ($SkipMomentum -and $comp.Name -eq "momentum_trader") {
        Write-Log "Skipping momentum_trader (--SkipMomentum)" "INFO"
        continue
    }

    $success = Start-Component $comp
    if (-not $success -and $comp.Required) {
        $failed += $comp.Name
    }
}

# Final health check
Write-Host ""
Start-Sleep -Seconds 2
$healthy = Test-SystemHealth

# Summary
Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "    STARTUP SUMMARY" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

if ($failed.Count -gt 0) {
    Write-Log "FAILED components: $($failed -join ', ')" "ERROR"
    exit 1
}

if ($healthy) {
    Write-Log "System HEALTHY - all components running" "INFO"
    Write-Host ""
    Write-Host "HOPE Trading System is LIVE!" -ForegroundColor Green
} else {
    Write-Log "System started but health check failed" "WARN"
}

Write-Host ""
