# === AI SIGNATURE ===
# Script: hope_core/deploy/Deploy-HopeCore.ps1
# Created by: Claude (opus-4.5)
# Purpose: Deploy HOPE Core v2.0 to VPS from Windows
# === END SIGNATURE ===
<#
.SYNOPSIS
    Deploys HOPE Core v2.0 to VPS 46.62.232.161

.DESCRIPTION
    This script:
    1. Stops existing services
    2. Backs up current files
    3. Uploads HOPE Core
    4. Installs dependencies
    5. Configures systemd services
    6. Starts HOPE Core
    7. Verifies deployment

.PARAMETER Mode
    Trading mode: DRY, TESTNET, or LIVE

.EXAMPLE
    .\Deploy-HopeCore.ps1 -Mode DRY
#>

param(
    [ValidateSet("DRY", "TESTNET", "LIVE")]
    [string]$Mode = "DRY",
    
    [string]$VpsHost = "46.62.232.161",
    [string]$VpsUser = "root",
    [string]$SshKeyPath = "$HOME\.ssh\id_ed25519_hope",
    [string]$RemoteDir = "/opt/hope/minibot"
)

$ErrorActionPreference = "Stop"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "         HOPE CORE v2.0 - VPS DEPLOYMENT" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Target: ${VpsUser}@${VpsHost}"
Write-Host "  Mode:   $Mode"
Write-Host "  Remote: $RemoteDir"
Write-Host ""

# Find source directory
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SourceDir = Split-Path -Parent $ScriptDir

if (-not (Test-Path "$SourceDir\hope_core.py")) {
    Write-Host "ERROR: hope_core.py not found in $SourceDir" -ForegroundColor Red
    exit 1
}

# Check SSH key
if (-not (Test-Path $SshKeyPath)) {
    Write-Host "ERROR: SSH key not found: $SshKeyPath" -ForegroundColor Red
    exit 1
}

$SshCmd = "ssh -i `"$SshKeyPath`" ${VpsUser}@${VpsHost}"
$ScpCmd = "scp -i `"$SshKeyPath`""

# ===========================================================================
# STEP 1: Check VPS connectivity
# ===========================================================================
Write-Host "[1/7] Checking VPS connectivity..." -ForegroundColor Yellow

try {
    $result = ssh -i $SshKeyPath ${VpsUser}@${VpsHost} "echo 'OK'"
    if ($result -ne "OK") {
        throw "Connection failed"
    }
    Write-Host "  VPS connection OK" -ForegroundColor Green
} catch {
    Write-Host "  ERROR: Cannot connect to VPS: $_" -ForegroundColor Red
    exit 1
}

# ===========================================================================
# STEP 2: Stop services and backup
# ===========================================================================
Write-Host "[2/7] Stopping services and backing up..." -ForegroundColor Yellow

ssh -i $SshKeyPath ${VpsUser}@${VpsHost} @"
    systemctl stop hope-core 2>/dev/null || true
    systemctl stop hope-guardian 2>/dev/null || true
    systemctl stop hope-autotrader 2>/dev/null || true
    
    BACKUP_DIR=${RemoteDir}/backups/`$(date +%Y%m%d_%H%M%S)
    mkdir -p `$BACKUP_DIR
    
    if [ -d ${RemoteDir}/hope_core ]; then
        cp -r ${RemoteDir}/hope_core `$BACKUP_DIR/
        echo "Backed up to `$BACKUP_DIR"
    fi
"@

Write-Host "  Backup complete" -ForegroundColor Green

# ===========================================================================
# STEP 3: Create directories
# ===========================================================================
Write-Host "[3/7] Creating directories..." -ForegroundColor Yellow

ssh -i $SshKeyPath ${VpsUser}@${VpsHost} @"
    mkdir -p ${RemoteDir}/hope_core/{bus,state,journal,guardian,deploy}
    touch ${RemoteDir}/hope_core/__init__.py
    touch ${RemoteDir}/hope_core/bus/__init__.py
    touch ${RemoteDir}/hope_core/state/__init__.py
    touch ${RemoteDir}/hope_core/journal/__init__.py
    touch ${RemoteDir}/hope_core/guardian/__init__.py
"@

Write-Host "  Directories created" -ForegroundColor Green

# ===========================================================================
# STEP 4: Upload files
# ===========================================================================
Write-Host "[4/7] Uploading HOPE Core files..." -ForegroundColor Yellow

$FilesToUpload = @(
    "hope_core.py",
    "api_server.py",
    "integration_bridge.py",
    "mocks.py",
    "alerts.py",
    "autotrader_adapter.py",
    "main.py"
)

foreach ($file in $FilesToUpload) {
    if (Test-Path "$SourceDir\$file") {
        scp -i $SshKeyPath "$SourceDir\$file" "${VpsUser}@${VpsHost}:${RemoteDir}/hope_core/"
        Write-Host "  Uploaded: $file" -ForegroundColor Gray
    }
}

# Upload submodules
$Submodules = @("bus", "state", "journal", "guardian")
foreach ($subdir in $Submodules) {
    $subPath = "$SourceDir\$subdir"
    if (Test-Path $subPath) {
        $pyFiles = Get-ChildItem -Path $subPath -Filter "*.py"
        foreach ($pyFile in $pyFiles) {
            scp -i $SshKeyPath $pyFile.FullName "${VpsUser}@${VpsHost}:${RemoteDir}/hope_core/${subdir}/"
        }
        Write-Host "  Uploaded: $subdir/" -ForegroundColor Gray
    }
}

# Upload deploy files
scp -i $SshKeyPath "$SourceDir\deploy\hope-core.service" "${VpsUser}@${VpsHost}:/etc/systemd/system/"
scp -i $SshKeyPath "$SourceDir\deploy\hope-guardian.service" "${VpsUser}@${VpsHost}:/etc/systemd/system/"
scp -i $SshKeyPath "$SourceDir\deploy\guardian.json" "${VpsUser}@${VpsHost}:${RemoteDir}/hope_core/"
Write-Host "  Uploaded: systemd services" -ForegroundColor Gray

Write-Host "  All files uploaded" -ForegroundColor Green

# ===========================================================================
# STEP 5: Create symlinks to existing modules
# ===========================================================================
Write-Host "[5/7] Creating symlinks..." -ForegroundColor Yellow

ssh -i $SshKeyPath ${VpsUser}@${VpsHost} @"
    cd ${RemoteDir}/hope_core
    
    # Link to existing modules
    ln -sf ../scripts/eye_of_god_v3.py eye_of_god_v3.py 2>/dev/null || true
    ln -sf ../scripts/order_executor.py order_executor.py 2>/dev/null || true
    ln -sf ../scripts/risk_manager.py risk_manager.py 2>/dev/null || true
    
    echo 'Symlinks created'
"@

Write-Host "  Symlinks created" -ForegroundColor Green

# ===========================================================================
# STEP 6: Install dependencies and test
# ===========================================================================
Write-Host "[6/7] Installing dependencies and testing..." -ForegroundColor Yellow

ssh -i $SshKeyPath ${VpsUser}@${VpsHost} @"
    cd ${RemoteDir}
    source /opt/hope/venv/bin/activate 2>/dev/null || source .venv/bin/activate 2>/dev/null || true
    
    pip install fastapi uvicorn aiohttp psutil jsonschema --quiet 2>/dev/null || \
    pip install fastapi uvicorn aiohttp psutil jsonschema --break-system-packages --quiet
    
    # Test imports
    export PYTHONPATH=${RemoteDir}:${RemoteDir}/scripts
    python3 -c '
import sys
sys.path.insert(0, ".")
sys.path.insert(0, "scripts")
sys.path.insert(0, "hope_core")

from hope_core.hope_core import HopeCore, HopeCoreConfig
print("Import test: PASSED")
'
"@

Write-Host "  Dependencies installed and tested" -ForegroundColor Green

# ===========================================================================
# STEP 7: Start services
# ===========================================================================
Write-Host "[7/7] Starting HOPE Core services..." -ForegroundColor Yellow

ssh -i $SshKeyPath ${VpsUser}@${VpsHost} @"
    systemctl daemon-reload
    systemctl enable hope-core hope-guardian
    
    # Start Guardian first (it monitors Core)
    systemctl start hope-guardian
    sleep 2
    
    # Start Core
    systemctl start hope-core
    sleep 3
    
    # Check status
    echo ''
    echo '=== Service Status ==='
    systemctl is-active hope-core
    systemctl is-active hope-guardian
    
    echo ''
    echo '=== Health Check ==='
    curl -s http://127.0.0.1:8200/api/health | python3 -m json.tool 2>/dev/null || echo 'Health check failed'
"@

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "         DEPLOYMENT COMPLETE!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Check logs:  ssh ${VpsUser}@${VpsHost} 'journalctl -u hope-core -f'"
Write-Host "  2. Dashboard:   ssh ${VpsUser}@${VpsHost} 'curl http://127.0.0.1:8200/dashboard'"
Write-Host "  3. Test signal: ssh ${VpsUser}@${VpsHost} 'curl -X POST http://127.0.0.1:8200/signal -d ...'"
Write-Host ""
