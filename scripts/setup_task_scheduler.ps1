# HOPE IPC Agents - Task Scheduler Setup
# Run as Administrator: powershell -ExecutionPolicy Bypass -File setup_task_scheduler.ps1

$ErrorActionPreference = "Stop"

$MINIBOT_DIR = "C:\Users\kirillDev\Desktop\TradingBot\minibot"
$SCRIPTS_DIR = "$MINIBOT_DIR\scripts"

# Task definitions
$tasks = @(
    @{
        Name = "HOPE-Claude-Agent"
        Description = "HOPE IPC Claude Agent - processes incoming tasks"
        Script = "$SCRIPTS_DIR\run_claude_agent.cmd"
        TriggerType = "AtLogOn"
    },
    @{
        Name = "HOPE-GPT-Agent"
        Description = "HOPE IPC GPT Agent - processes incoming tasks"
        Script = "$SCRIPTS_DIR\run_gpt_agent.cmd"
        TriggerType = "AtLogOn"
    },
    @{
        Name = "HOPE-Morning-Scan"
        Description = "HOPE Daily Morning Scan - market intel at 10:00 AM"
        Script = "$SCRIPTS_DIR\run_morning_scan.cmd"
        TriggerType = "Daily"
        TriggerTime = "10:00AM"
    },
    @{
        Name = "HOPE-TG-Bot"
        Description = "HOPE Telegram Bot - admin panel and notifications"
        Script = "$SCRIPTS_DIR\run_tg_bot.cmd"
        TriggerType = "AtLogOn"
    },
    @{
        Name = "HOPE-Friend-Bridge"
        Description = "HOPE Friend Bridge - localhost HTTP for chat CLI"
        Script = "$SCRIPTS_DIR\run_friend_bridge.cmd"
        TriggerType = "AtLogOn"
    }
)

Write-Host "=== HOPE Task Scheduler Setup ===" -ForegroundColor Cyan
Write-Host ""

foreach ($task in $tasks) {
    Write-Host "Setting up: $($task.Name)" -ForegroundColor Yellow

    # Remove existing task if present
    $existing = Get-ScheduledTask -TaskName $task.Name -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "  Removing existing task..."
        Unregister-ScheduledTask -TaskName $task.Name -Confirm:$false
    }

    # Create action - run cmd script
    $action = New-ScheduledTaskAction -Execute "cmd.exe" `
        -Argument "/c `"$($task.Script)`"" `
        -WorkingDirectory $MINIBOT_DIR

    # Create trigger based on type
    if ($task.TriggerType -eq "Daily") {
        $trigger = New-ScheduledTaskTrigger -Daily -At $task.TriggerTime
    } else {
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    }

    # Settings
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit (New-TimeSpan -Days 365)

    # Principal - run only when logged on (shows console window)
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive

    # Register task
    Register-ScheduledTask `
        -TaskName $task.Name `
        -Description $task.Description `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal

    Write-Host "  Created: $($task.Name)" -ForegroundColor Green
}

Write-Host ""
Write-Host "=== Setup Complete ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Tasks will start automatically at next logon."
Write-Host "To start now, run:" -ForegroundColor Yellow
Write-Host "  Start-ScheduledTask -TaskName 'HOPE-Claude-Agent'"
Write-Host "  Start-ScheduledTask -TaskName 'HOPE-GPT-Agent'"
Write-Host ""
Write-Host "To check status:" -ForegroundColor Yellow
Write-Host "  Get-ScheduledTask -TaskName 'HOPE-*' | Format-Table Name, State"
