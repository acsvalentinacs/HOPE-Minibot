#!/bin/bash
# === AI SIGNATURE ===
# Script: hope_core/deploy/deploy_to_vps.sh
# Created by: Claude (opus-4.5)
# Purpose: Deploy HOPE Core v2.0 to VPS
# === END SIGNATURE ===

set -e

# Configuration
VPS_HOST="${VPS_HOST:-46.62.232.161}"
VPS_USER="${VPS_USER:-root}"
SSH_KEY="${SSH_KEY:-~/.ssh/id_ed25519_hope}"
REMOTE_DIR="/opt/hope/minibot"
LOCAL_DIR="$(dirname "$0")/.."

echo "╔══════════════════════════════════════════════════════════╗"
echo "║       HOPE Core v2.0 - Production Deployment             ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
echo "Target: ${VPS_USER}@${VPS_HOST}"
echo "Remote: ${REMOTE_DIR}"
echo ""

# Check SSH key
if [ ! -f "$SSH_KEY" ]; then
    echo "ERROR: SSH key not found: $SSH_KEY"
    echo "Please set SSH_KEY environment variable"
    exit 1
fi

SSH_CMD="ssh -i $SSH_KEY -o ConnectTimeout=10 ${VPS_USER}@${VPS_HOST}"
SCP_CMD="scp -i $SSH_KEY -o ConnectTimeout=10"

# Check connectivity
echo "[0/8] Testing connectivity..."
if ! $SSH_CMD "echo 'Connected'" > /dev/null 2>&1; then
    echo "ERROR: Cannot connect to VPS"
    exit 1
fi
echo "     ✓ VPS accessible"

# Step 1: Stop existing services
echo ""
echo "[1/8] Stopping existing services..."
$SSH_CMD "systemctl stop hope-autotrader hope-core hope-guardian 2>/dev/null || true"
echo "     ✓ Services stopped"

# Step 2: Backup existing code
echo ""
echo "[2/8] Creating backup..."
BACKUP_NAME="hope_backup_$(date +%Y%m%d_%H%M%S)"
$SSH_CMD "
    cd ${REMOTE_DIR}
    if [ -d hope_core ]; then
        tar -czf ${BACKUP_NAME}.tar.gz hope_core 2>/dev/null || true
        echo 'Backup: ${BACKUP_NAME}.tar.gz'
    fi
"
echo "     ✓ Backup created"

# Step 3: Create remote directories
echo ""
echo "[3/8] Preparing directories..."
$SSH_CMD "
    mkdir -p ${REMOTE_DIR}/hope_core/{bus,state,journal,guardian,alerts,metrics,static,deploy}
    mkdir -p ${REMOTE_DIR}/state/events
    mkdir -p ${REMOTE_DIR}/logs
"
echo "     ✓ Directories ready"

# Step 4: Copy all files
echo ""
echo "[4/8] Deploying code..."

# Create tar locally and send
cd "$LOCAL_DIR"
tar -czf /tmp/hope_core_deploy.tar.gz \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='state/events/*' \
    *.py \
    bus/*.py \
    state/*.py \
    journal/*.py \
    guardian/*.py \
    alerts/*.py \
    metrics/*.py \
    static/*.html \
    deploy/*.service \
    deploy/*.json \
    deploy/*.sh \
    2>/dev/null || true

$SCP_CMD /tmp/hope_core_deploy.tar.gz "${VPS_USER}@${VPS_HOST}:/tmp/"

$SSH_CMD "
    cd ${REMOTE_DIR}/hope_core
    tar -xzf /tmp/hope_core_deploy.tar.gz
    rm /tmp/hope_core_deploy.tar.gz
    
    # Create __init__.py files
    touch __init__.py bus/__init__.py state/__init__.py journal/__init__.py
    touch guardian/__init__.py alerts/__init__.py metrics/__init__.py
"
echo "     ✓ Code deployed"

# Step 5: Install dependencies
echo ""
echo "[5/8] Installing dependencies..."
$SSH_CMD "
    source /opt/hope/venv/bin/activate 2>/dev/null || true
    pip install fastapi uvicorn aiohttp psutil jsonschema --quiet --break-system-packages 2>/dev/null || \
    pip3 install fastapi uvicorn aiohttp psutil jsonschema --quiet 2>/dev/null || true
"
echo "     ✓ Dependencies installed"

# Step 6: Install systemd services
echo ""
echo "[6/8] Installing services..."
$SSH_CMD "
    cp ${REMOTE_DIR}/hope_core/deploy/hope-core.service /etc/systemd/system/
    cp ${REMOTE_DIR}/hope_core/deploy/hope-guardian.service /etc/systemd/system/
    cp ${REMOTE_DIR}/hope_core/deploy/guardian.json ${REMOTE_DIR}/config/ 2>/dev/null || \
        mkdir -p ${REMOTE_DIR}/config && cp ${REMOTE_DIR}/hope_core/deploy/guardian.json ${REMOTE_DIR}/config/
    
    systemctl daemon-reload
    systemctl enable hope-core hope-guardian
"
echo "     ✓ Services installed"

# Step 7: Verify deployment
echo ""
echo "[7/8] Verifying deployment..."
$SSH_CMD "
    cd ${REMOTE_DIR}
    
    # Check Python syntax
    python3 -m py_compile hope_core/hope_core.py && echo '     ✓ hope_core.py OK' || echo '     ✗ hope_core.py FAILED'
    python3 -m py_compile hope_core/api_server.py && echo '     ✓ api_server.py OK' || echo '     ✗ api_server.py FAILED'
    
    # Check imports
    python3 -c '
import sys
sys.path.insert(0, \".\")
from hope_core.hope_core import HopeCore
print(\"     ✓ Imports OK\")
' 2>/dev/null || echo '     ✗ Import check FAILED'
"

# Step 8: Start services
echo ""
echo "[8/8] Starting services..."
$SSH_CMD "
    systemctl start hope-guardian
    sleep 2
    systemctl start hope-core
    sleep 5
"

# Final verification
echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║                   DEPLOYMENT COMPLETE                     ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

$SSH_CMD "
    echo 'Service Status:'
    echo '  hope-core:     '$(systemctl is-active hope-core)
    echo '  hope-guardian: '$(systemctl is-active hope-guardian)
    echo ''
    
    # Health check
    echo 'Health Check:'
    curl -s http://127.0.0.1:8200/api/health 2>/dev/null | python3 -c '
import sys, json
try:
    data = json.load(sys.stdin)
    print(f\"  Status: {data.get(\"status\", \"unknown\")}\")
    print(f\"  Mode: {data.get(\"mode\", \"unknown\")}\")
    print(f\"  Uptime: {data.get(\"uptime_seconds\", 0):.0f}s\")
except:
    print(\"  Health endpoint not responding yet\")
'
"

echo ""
echo "Useful commands:"
echo "  systemctl status hope-core"
echo "  journalctl -u hope-core -f"
echo "  curl http://127.0.0.1:8200/status"
echo "  curl http://127.0.0.1:8200/dashboard"
echo ""

rm -f /tmp/hope_core_deploy.tar.gz
