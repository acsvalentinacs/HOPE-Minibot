# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-23 21:00:00 UTC
# === END SIGNATURE ===
#
# HOPE Chat Pipeline - Task Scheduler Setup
# Creates scheduled task that runs at logon
#
# Run as Administrator:
#   powershell -ExecutionPolicy Bypass -File scripts\setup_chat_pipeline_task.ps1

$ErrorActionPreference = "Stop"

$TASK_NAME = "HOPE-ChatPipeline"
$MINIBOT_DIR = "C:\Users\kirillDev\Desktop\TradingBot\minibot"
$SCRIPT_PATH = "$MINIBOT_DIR\scripts\run_chat_pipeline.cmd"

Write-Host ""
Write-Host "=== HOPE Chat Pipeline - Task Scheduler Setup ===" -ForegroundColor Cyan
Write-Host ""

# Check if running as admin
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[ERROR] Run this script as Administrator!" -ForegroundColor Red
    Write-Host ""
    Write-Host "Right-click PowerShell -> Run as Administrator, then:" -ForegroundColor Yellow
    Write-Host "  cd $MINIBOT_DIR" -ForegroundColor Gray
    Write-Host "  powershell -ExecutionPolicy Bypass -File scripts\setup_chat_pipeline_task.ps1" -ForegroundColor Gray
    exit 1
}

# Remove existing task if present
$existing = Get-ScheduledTask -TaskName $TASK_NAME -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "[INFO] Removing existing task '$TASK_NAME'..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TASK_NAME -Confirm:$false
}

# Create action
$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"$SCRIPT_PATH`"" `
    -WorkingDirectory $MINIBOT_DIR

# Create trigger - at logon
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

# Settings - long-running, restart on failure
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 365) `
    -MultipleInstances IgnoreNew

# Principal - run only when logged on (to see console windows)
$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

# Register task
Register-ScheduledTask `
    -TaskName $TASK_NAME `
    -Description "HOPE Chat Pipeline - GPT Orchestrator + Claude Executor" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal | Out-Null

Write-Host ""
Write-Host "[OK] Task '$TASK_NAME' created!" -ForegroundColor Green
Write-Host ""
Write-Host "Task will auto-start at next logon." -ForegroundColor Cyan
Write-Host ""
Write-Host "Commands:" -ForegroundColor Yellow
Write-Host "  Start now:    Start-ScheduledTask -TaskName '$TASK_NAME'" -ForegroundColor Gray
Write-Host "  Stop:         Stop-ScheduledTask -TaskName '$TASK_NAME'" -ForegroundColor Gray
Write-Host "  Check status: Get-ScheduledTask -TaskName '$TASK_NAME' | Select State" -ForegroundColor Gray
Write-Host "  Remove:       Unregister-ScheduledTask -TaskName '$TASK_NAME' -Confirm:`$false" -ForegroundColor Gray
Write-Host ""

# Ask to start now
$start = Read-Host "Start pipeline now? (y/n)"
if ($start -eq "y" -or $start -eq "Y") {
    Write-Host ""
    Write-Host "[INFO] Starting task..." -ForegroundColor Yellow
    Start-ScheduledTask -TaskName $TASK_NAME
    Start-Sleep -Seconds 3

    $task = Get-ScheduledTask -TaskName $TASK_NAME
    Write-Host "[OK] Task state: $($task.State)" -ForegroundColor Green
}

Write-Host ""
Write-Host "=== Setup Complete ===" -ForegroundColor Cyan
Write-Host ""
