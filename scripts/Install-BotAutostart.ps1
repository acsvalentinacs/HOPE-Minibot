# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-26T10:00:00Z
# Modified at: 2026-01-26T10:25:00Z
# Purpose: Install Telegram Bot autostart task (robust version)
# === END SIGNATURE ===

<#
.SYNOPSIS
    Installs HOPE Telegram Bot autostart task in Task Scheduler.
.DESCRIPTION
    Creates a scheduled task that starts the bot on user logon with 45 second delay.
    Requires Administrator privileges. Handles all edge cases robustly.
#>

# DO NOT use Stop globally - handle errors explicitly
$ErrorActionPreference = "Continue"

function Write-Status {
    param([string]$Message, [string]$Color = "White")
    Write-Host $Message -ForegroundColor $Color
}

function Write-Success { param([string]$Message) Write-Status $Message "Green" }
function Write-Error2 { param([string]$Message) Write-Status $Message "Red" }
function Write-Warning2 { param([string]$Message) Write-Status $Message "Yellow" }
function Write-Info { param([string]$Message) Write-Status $Message "Cyan" }
function Write-Detail { param([string]$Message) Write-Status $Message "Gray" }

# ============================================================
# CONFIGURATION
# ============================================================
$TaskFolder = "HOPE"
$TaskName = "TelegramBot"
$FullTaskName = "$TaskFolder\$TaskName"
$ScriptPath = "C:\Users\kirillDev\Desktop\TradingBot\minibot\scripts\start_tg_bot.cmd"
$WorkingDir = "C:\Users\kirillDev\Desktop\TradingBot\minibot"

# ============================================================
# ADMIN CHECK
# ============================================================
$currentPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
$isAdmin = $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Host ""
    Write-Error2 "============================================================"
    Write-Error2 "  ERROR: This script requires Administrator privileges!"
    Write-Error2 "============================================================"
    Write-Host ""
    Write-Warning2 "Please run PowerShell as Administrator and try again."
    Write-Host ""
    exit 1
}

# ============================================================
# HEADER
# ============================================================
Write-Host ""
Write-Info "============================================================"
Write-Info "  HOPE Telegram Bot - Autostart Installer v2"
Write-Info "============================================================"
Write-Host ""
Write-Detail "Task:        $FullTaskName"
Write-Detail "Script:      $ScriptPath"
Write-Detail "Trigger:     On logon (45 sec delay)"
Write-Host ""

# ============================================================
# VERIFY SCRIPT EXISTS
# ============================================================
if (-not (Test-Path $ScriptPath -PathType Leaf)) {
    Write-Error2 "ERROR: Start script not found!"
    Write-Error2 "Expected: $ScriptPath"
    exit 1
}
Write-Success "[OK] Start script exists"

# ============================================================
# CLEANUP OLD TASK (multiple methods, ignore errors)
# ============================================================
Write-Warning2 "Cleaning up old task (if any)..."

# Method 1: PowerShell cmdlet
try {
    $existingTask = Get-ScheduledTask -TaskName $TaskName -TaskPath "\$TaskFolder\" -ErrorAction SilentlyContinue
    if ($existingTask) {
        Unregister-ScheduledTask -TaskName $TaskName -TaskPath "\$TaskFolder\" -Confirm:$false -ErrorAction SilentlyContinue
        Write-Detail "  Removed via PowerShell cmdlet"
    }
} catch {
    # Ignore - task may not exist
}

# Method 2: schtasks.exe (backup method, suppress all output)
$null = & schtasks /delete /tn $FullTaskName /f 2>$null
# Don't check exit code - we don't care if it didn't exist

Write-Success "[OK] Cleanup complete"

# ============================================================
# CREATE TASK VIA XML (most reliable method)
# ============================================================
Write-Warning2 "Creating scheduled task..."

# Generate XML for task (this is the most reliable way)
$xmlContent = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>HOPE Telegram Bot - auto-start on logon</Description>
    <Author>HOPE</Author>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <Delay>PT45S</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>$ScriptPath</Command>
      <WorkingDirectory>$WorkingDir</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

# Save XML to temp file
$tempXmlPath = Join-Path $env:TEMP "hope_bot_task.xml"
try {
    # Write with UTF-16 encoding (required by schtasks)
    [System.IO.File]::WriteAllText($tempXmlPath, $xmlContent, [System.Text.Encoding]::Unicode)
    Write-Detail "  XML saved to: $tempXmlPath"
} catch {
    Write-Error2 "ERROR: Failed to write XML file"
    Write-Error2 $_.Exception.Message
    exit 1
}

# Create task from XML
$createOutput = & schtasks /create /xml $tempXmlPath /tn $FullTaskName /f 2>&1
$createExitCode = $LASTEXITCODE

# Clean up temp file
Remove-Item $tempXmlPath -Force -ErrorAction SilentlyContinue

if ($createExitCode -ne 0) {
    Write-Error2 "ERROR: Failed to create task!"
    Write-Error2 "schtasks output: $createOutput"
    Write-Host ""
    Write-Warning2 "Manual fix: Open Task Scheduler (taskschd.msc) and create task manually."
    exit 1
}

Write-Success "[OK] Task created"

# ============================================================
# VERIFY TASK EXISTS
# ============================================================
Write-Warning2 "Verifying..."

$verifyOutput = & schtasks /query /tn $FullTaskName 2>&1
$verifyExitCode = $LASTEXITCODE

if ($verifyExitCode -eq 0) {
    Write-Success "[OK] Task verified in Task Scheduler"
} else {
    Write-Error2 "WARNING: Could not verify task"
    Write-Detail "  This might be a false alarm. Check Task Scheduler manually."
}

# ============================================================
# SUCCESS
# ============================================================
Write-Host ""
Write-Success "============================================================"
Write-Success "  SUCCESS! Bot will auto-start when you log in."
Write-Success "============================================================"
Write-Host ""
Write-Status "Commands:" "White"
Write-Info "  Start now:    schtasks /run /tn `"$FullTaskName`""
Write-Info "  Check status: schtasks /query /tn `"$FullTaskName`""
Write-Info "  Remove task:  schtasks /delete /tn `"$FullTaskName`" /f"
Write-Host ""
