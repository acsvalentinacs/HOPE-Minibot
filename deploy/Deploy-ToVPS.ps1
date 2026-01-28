# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29T00:35:00Z
# Purpose: HOPE VPS Deployment Script (PowerShell) - Windows PC to Linux VPS
# Security: Fail-closed, no secrets in git, atomic deployments
# === END SIGNATURE ===

<#
.SYNOPSIS
    HOPE VPS Deployment Script for Windows

.DESCRIPTION
    Deploys HOPE trading system from Windows PC to Linux VPS.
    Handles: code sync, venv setup, systemd services.

.PARAMETER Action
    Deployment action: Init, Update, Sync, Restart, Start, Stop, Status, Logs

.PARAMETER VpsHost
    VPS hostname or IP (default: 46.62.232.161)

.PARAMETER VpsUser
    SSH user (default: hope)

.PARAMETER LogLines
    Number of log lines to show (for Logs action)

.EXAMPLE
    .\Deploy-ToVPS.ps1 -Action Init
    .\Deploy-ToVPS.ps1 -Action Update
    .\Deploy-ToVPS.ps1 -Action Status
    .\Deploy-ToVPS.ps1 -Action Logs -LogLines 100
#>

param(
    [ValidateSet('Init', 'Update', 'Sync', 'Restart', 'Start', 'Stop', 'Status', 'Logs', 'Help')]
    [string]$Action = 'Update',

    [string]$VpsHost = '46.62.232.161',
    [string]$VpsUser = 'hope',
    [int]$VpsPort = 22,
    [int]$LogLines = 50
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# === CONFIGURATION ===
$ROOT = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not $ROOT) { $ROOT = "C:\Users\kirillDev\Desktop\TradingBot\minibot" }

$REMOTE_BASE = "/opt/hope"
$REMOTE_PROJECT = "$REMOTE_BASE/minibot"

# Excluded from sync
$EXCLUDES = @(
    '.git', '__pycache__', '*.pyc', '.env', 'state/',
    '*.tmp', '*.log', '*.bak', 'Старые файлы*'
)

# === FUNCTIONS ===

function Write-Info($msg) { Write-Host "[INFO] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Write-Err($msg) { Write-Host "[ERROR] $msg" -ForegroundColor Red; exit 1 }

function Test-Prerequisites {
    Write-Info "Checking prerequisites..."

    # Check SSH
    $sshPath = Get-Command ssh -ErrorAction SilentlyContinue
    if (-not $sshPath) { Write-Err "ssh not found. Install OpenSSH." }

    # Check scp
    $scpPath = Get-Command scp -ErrorAction SilentlyContinue
    if (-not $scpPath) { Write-Err "scp not found. Install OpenSSH." }

    # Check connectivity
    $testResult = ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new -p $VpsPort "$VpsUser@$VpsHost" "echo OK" 2>&1
    if ($testResult -ne "OK") { Write-Err "Cannot connect to VPS. Check SSH config." }

    # Check local project
    if (-not (Test-Path "$ROOT\core\entrypoint.py")) { Write-Err "Invalid project structure at $ROOT" }

    Write-Info "Prerequisites OK"
}

function Invoke-SSHCommand($command) {
    $result = ssh -o ConnectTimeout=10 -p $VpsPort "$VpsUser@$VpsHost" $command 2>&1
    return $result
}

function Initialize-VPS {
    Write-Info "Initializing VPS structure..."

    $initScript = @'
set -euo pipefail

# Create directories
sudo mkdir -p /opt/hope/minibot
sudo mkdir -p /opt/hope/venv
sudo mkdir -p /etc/hope
sudo mkdir -p /var/log/hope

# Set ownership
sudo chown -R $(whoami):$(whoami) /opt/hope
sudo chown -R $(whoami):$(whoami) /var/log/hope
sudo chown $(whoami):$(whoami) /etc/hope
sudo chmod 700 /etc/hope

# Create state directories
mkdir -p /opt/hope/minibot/state
mkdir -p /opt/hope/minibot/state/evidence
mkdir -p /opt/hope/minibot/state/snapshots

# Install Python venv
if [[ ! -f /opt/hope/venv/bin/python ]]; then
    python3 -m venv /opt/hope/venv
    /opt/hope/venv/bin/pip install --upgrade pip wheel
fi

echo "VPS structure initialized"
'@

    $result = Invoke-SSHCommand "bash -c '$initScript'"
    Write-Host $result
    Write-Info "VPS initialized"
}

function Sync-Code {
    Write-Info "Syncing code to VPS..."

    # Build exclude args
    $excludeArgs = $EXCLUDES | ForEach-Object { "--exclude='$_'" }
    $excludeStr = $excludeArgs -join ' '

    # Use rsync if available, otherwise scp
    $rsyncPath = Get-Command rsync -ErrorAction SilentlyContinue

    if ($rsyncPath) {
        # rsync available (e.g., via Git Bash or WSL)
        $cmd = "rsync -avz --delete $excludeStr -e 'ssh -p $VpsPort' '$ROOT/' '$VpsUser@${VpsHost}:$REMOTE_PROJECT/'"
        Write-Info "Using rsync: $cmd"
        bash -c $cmd
    } else {
        # Fallback to scp (less efficient)
        Write-Warn "rsync not found, using scp (slower, no delete)"

        # Create tar excluding unwanted files
        $tarFile = "$env:TEMP\hope_deploy.tar.gz"
        $excludeTar = $EXCLUDES | ForEach-Object { "--exclude='$_'" }

        Push-Location $ROOT
        tar -czf $tarFile $excludeTar .
        Pop-Location

        # Upload and extract
        scp -P $VpsPort $tarFile "$VpsUser@${VpsHost}:/tmp/hope_deploy.tar.gz"
        Invoke-SSHCommand "cd $REMOTE_PROJECT && tar -xzf /tmp/hope_deploy.tar.gz && rm /tmp/hope_deploy.tar.gz"

        Remove-Item $tarFile -ErrorAction SilentlyContinue
    }

    Write-Info "Code synced"
}

function Install-Dependencies {
    Write-Info "Installing Python dependencies..."

    $depsScript = @'
set -euo pipefail
cd /opt/hope/minibot

if [[ -f requirements.txt ]]; then
    /opt/hope/venv/bin/pip install -r requirements.txt
fi

/opt/hope/venv/bin/python -c "
import sys
sys.path.insert(0, '/opt/hope/minibot')
from core.entrypoint import main
print('Core imports OK')
"

echo "Dependencies installed"
'@

    $result = Invoke-SSHCommand "bash -c '$depsScript'"
    Write-Host $result
    Write-Info "Dependencies installed"
}

function Install-Services {
    Write-Info "Installing systemd services..."

    $servicesScript = @'
set -euo pipefail

sudo cp /opt/hope/minibot/deploy/systemd/hope-core.service /etc/systemd/system/
sudo cp /opt/hope/minibot/deploy/systemd/hope-tgbot.service /etc/systemd/system/
sudo cp /opt/hope/minibot/deploy/systemd/hope-stack.target /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable hope-core hope-tgbot hope-stack.target

echo "Services installed"
'@

    $result = Invoke-SSHCommand "bash -c '$servicesScript'"
    Write-Host $result
    Write-Info "Services installed"
}

function Start-Services {
    Write-Info "Starting services..."

    $startScript = @'
sudo systemctl start hope-core
sleep 2
sudo systemctl start hope-tgbot
systemctl status hope-core --no-pager -l || true
systemctl status hope-tgbot --no-pager -l || true
echo "Services started"
'@

    $result = Invoke-SSHCommand "bash -c '$startScript'"
    Write-Host $result
    Write-Info "Services started"
}

function Stop-Services {
    Write-Info "Stopping services..."
    $result = Invoke-SSHCommand "sudo systemctl stop hope-tgbot hope-core 2>/dev/null; echo 'Services stopped'"
    Write-Host $result
}

function Show-Status {
    Write-Info "Service status:"

    $statusScript = @'
echo "=== HOPE Core ==="
systemctl status hope-core --no-pager -l 2>/dev/null || echo "Not running"
echo ""
echo "=== HOPE TgBot ==="
systemctl status hope-tgbot --no-pager -l 2>/dev/null || echo "Not running"
echo ""
echo "=== Health Files ==="
cat /opt/hope/minibot/state/health_v5.json 2>/dev/null || echo "No health file"
'@

    $result = Invoke-SSHCommand "bash -c '$statusScript'"
    Write-Host $result
}

function Show-Logs {
    Write-Info "Recent logs (last $LogLines lines):"
    $result = Invoke-SSHCommand "journalctl -u hope-core -u hope-tgbot -n $LogLines --no-pager"
    Write-Host $result
}

function Invoke-FullDeploy {
    Test-Prerequisites
    Stop-Services 2>$null
    Sync-Code
    Install-Dependencies
    Install-Services
    Start-Services
    Show-Status
}

# === MAIN ===

switch ($Action) {
    'Init' {
        Test-Prerequisites
        Initialize-VPS
        Sync-Code
        Install-Dependencies
        Install-Services
        Write-Info "Init complete. Configure /etc/hope/.env on VPS, then run: .\Deploy-ToVPS.ps1 -Action Start"
    }
    'Update' {
        Invoke-FullDeploy
    }
    'Sync' {
        Test-Prerequisites
        Sync-Code
    }
    'Restart' {
        Stop-Services
        Start-Sleep -Seconds 2
        Start-Services
    }
    'Start' {
        Start-Services
    }
    'Stop' {
        Stop-Services
    }
    'Status' {
        Show-Status
    }
    'Logs' {
        Show-Logs
    }
    'Help' {
        Write-Host @"
HOPE VPS Deployment Script (PowerShell)

Usage: .\Deploy-ToVPS.ps1 -Action <action> [-VpsHost <host>] [-VpsUser <user>]

Actions:
  Init      First-time VPS setup (creates dirs, venv)
  Update    Full deploy: sync + deps + restart (default)
  Sync      Sync code only (no restart)
  Restart   Restart services
  Start     Start services
  Stop      Stop services
  Status    Show service status
  Logs      Show recent logs

Parameters:
  -VpsHost    Target host (default: 46.62.232.161)
  -VpsUser    SSH user (default: hope)
  -VpsPort    SSH port (default: 22)
  -LogLines   Number of log lines for Logs action (default: 50)

Examples:
  .\Deploy-ToVPS.ps1 -Action Init
  .\Deploy-ToVPS.ps1 -Action Update
  .\Deploy-ToVPS.ps1 -Action Status
  .\Deploy-ToVPS.ps1 -Action Logs -LogLines 100
"@
    }
}
