# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at (UTC): 2026-01-25T16:45:00Z
# Purpose: News Spider launcher for Task Scheduler (PowerShell version)
# === END SIGNATURE ===

<#
.SYNOPSIS
    HOPE News Spider Auto-Runner

.DESCRIPTION
    Runs News Spider v1.1 to collect news and optionally publish to Telegram.
    Designed for Windows Task Scheduler (every 15-30 minutes).

.PARAMETER Mode
    Collector mode: "strict" or "lenient" (default: lenient)

.PARAMETER DryRun
    If specified, don't publish to Telegram (default: $true)

.PARAMETER Publish
    If specified, actually publish to Telegram

.EXAMPLE
    .\run_spider.ps1                      # Dry-run mode
    .\run_spider.ps1 -Publish             # Actually publish
    .\run_spider.ps1 -Mode strict         # Strict mode (fail on any error)
#>

param(
    [ValidateSet("strict", "lenient")]
    [string]$Mode = "lenient",

    [switch]$DryRun,
    [switch]$Publish
)

$ErrorActionPreference = "Stop"

# Paths
$ProjectRoot = "C:\Users\kirillDev\Desktop\TradingBot\minibot"
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $ProjectRoot "logs"
$LogFile = Join-Path $LogDir "spider_auto.log"
$StopFlag = "C:\Users\kirillDev\Desktop\TradingBot\flags\STOP.flag"

# Ensure log directory exists
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

function Write-Log {
    param([string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $logLine = "[$timestamp] $Message"
    Add-Content -Path $LogFile -Value $logLine -Encoding UTF8
    Write-Host $logLine
}

# Check STOP flag
if (Test-Path $StopFlag) {
    Write-Log "STOP.flag detected - skipping spider run"
    exit 0
}

# Validate venv
if (-not (Test-Path $VenvPython)) {
    Write-Log "ERROR: Python venv not found at $VenvPython"
    exit 1
}

# Set working directory
Set-Location $ProjectRoot

# Build command arguments
$args = @("-m", "core.spider.telegram_bridge", "--collect", "--mode", $Mode)

if ($Publish) {
    Write-Log "MODE: PUBLISH (will send to Telegram)"
    $args += "--publish"
} else {
    Write-Log "MODE: DRY-RUN (no Telegram publish)"
    $args += "--dry-run"
}

Write-Log "Starting Spider: $VenvPython $($args -join ' ')"

try {
    $startTime = Get-Date

    # Run spider
    $output = & $VenvPython $args 2>&1
    $exitCode = $LASTEXITCODE

    $duration = (Get-Date) - $startTime

    # Log output
    foreach ($line in $output) {
        Write-Log "  $line"
    }

    if ($exitCode -eq 0) {
        Write-Log "Spider completed successfully (duration: $($duration.TotalSeconds.ToString('F1'))s)"
    } else {
        Write-Log "Spider failed with exit code $exitCode (duration: $($duration.TotalSeconds.ToString('F1'))s)"
    }

    exit $exitCode
}
catch {
    Write-Log "EXCEPTION: $_"
    exit 1
}
