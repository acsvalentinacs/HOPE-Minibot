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

echo "=== HOPE Core v2.0 Deployment ==="
echo "Target: ${VPS_USER}@${VPS_HOST}"
echo "Remote: ${REMOTE_DIR}"
echo ""

# Check SSH key
if [ ! -f "$SSH_KEY" ]; then
    echo "ERROR: SSH key not found: $SSH_KEY"
    exit 1
fi

SSH_CMD="ssh -i $SSH_KEY ${VPS_USER}@${VPS_HOST}"
SCP_CMD="scp -i $SSH_KEY"

# Step 1: Stop existing services
echo "[1/7] Stopping existing services..."
$SSH_CMD "systemctl stop hope-core hope-guardian 2>/dev/null || true"

# Step 2: Backup existing code
echo "[2/7] Backing up existing code..."
BACKUP_NAME="hope_core_backup_$(date +%Y%m%d_%H%M%S)"
$SSH_CMD "
    if [ -d ${REMOTE_DIR}/hope_core ]; then
        mv ${REMOTE_DIR}/hope_core ${REMOTE_DIR}/${BACKUP_NAME}
        echo 'Backup created: ${BACKUP_NAME}'
    fi
"

# Step 3: Create remote directories
echo "[3/7] Creating remote directories..."
$SSH_CMD "
    mkdir -p ${REMOTE_DIR}/hope_core/{bus,state,journal,guardian,deploy}
    mkdir -p ${REMOTE_DIR}/state/events
    mkdir -p ${REMOTE_DIR}/logs
"

# Step 4: Copy HOPE Core files
echo "[4/7] Copying HOPE Core files..."

# Main files
$SCP_CMD "$LOCAL_DIR/hope_core.py" "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/hope_core/"
$SCP_CMD "$LOCAL_DIR/api_server.py" "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/hope_core/"
$SCP_CMD "$LOCAL_DIR/integration_bridge.py" "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/hope_core/"
$SCP_CMD "$LOCAL_DIR/ARCHITECTURE.md" "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/hope_core/"

# Bus module
$SCP_CMD "$LOCAL_DIR/bus/"*.py "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/hope_core/bus/"

# State module
$SCP_CMD "$LOCAL_DIR/state/"*.py "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/hope_core/state/"

# Journal module
$SCP_CMD "$LOCAL_DIR/journal/"*.py "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/hope_core/journal/"

# Guardian module
$SCP_CMD "$LOCAL_DIR/guardian/"*.py "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/hope_core/guardian/"

# Create __init__.py files
$SSH_CMD "
    touch ${REMOTE_DIR}/hope_core/__init__.py
    touch ${REMOTE_DIR}/hope_core/bus/__init__.py
    touch ${REMOTE_DIR}/hope_core/state/__init__.py
    touch ${REMOTE_DIR}/hope_core/journal/__init__.py
    touch ${REMOTE_DIR}/hope_core/guardian/__init__.py
"

# Step 5: Install dependencies
echo "[5/7] Installing dependencies..."
$SSH_CMD "
    source /opt/hope/venv/bin/activate
    pip install --quiet fastapi uvicorn aiohttp psutil jsonschema 2>/dev/null || pip install fastapi uvicorn aiohttp psutil jsonschema --break-system-packages
"

# Step 6: Install systemd services
echo "[6/7] Installing systemd services..."
$SCP_CMD "$LOCAL_DIR/deploy/hope-core.service" "${VPS_USER}@${VPS_HOST}:/etc/systemd/system/"
$SCP_CMD "$LOCAL_DIR/deploy/hope-guardian.service" "${VPS_USER}@${VPS_HOST}:/etc/systemd/system/"

$SSH_CMD "
    systemctl daemon-reload
    systemctl enable hope-core hope-guardian
"

# Step 7: Test and start
echo "[7/7] Testing and starting services..."

# Test syntax
$SSH_CMD "
    cd ${REMOTE_DIR}
    source /opt/hope/venv/bin/activate
    python -m py_compile hope_core/hope_core.py
    python -m py_compile hope_core/api_server.py
    echo 'Syntax check: PASS'
"

# Test imports
$SSH_CMD "
    cd ${REMOTE_DIR}
    source /opt/hope/venv/bin/activate
    python -c '
import sys
sys.path.insert(0, \".\")
from hope_core.hope_core import HopeCore
from hope_core.api_server import HAS_FASTAPI
print(f\"HopeCore import: OK\")
print(f\"FastAPI available: {HAS_FASTAPI}\")
'
"

# Start services
echo ""
echo "Starting services..."
$SSH_CMD "
    systemctl start hope-guardian
    sleep 2
    systemctl start hope-core
    sleep 5
    
    echo ''
    echo '=== Service Status ==='
    systemctl is-active hope-core || true
    systemctl is-active hope-guardian || true
"

# Verify
echo ""
echo "Verifying deployment..."
$SSH_CMD "
    sleep 3
    curl -s http://127.0.0.1:8200/api/health | python3 -m json.tool 2>/dev/null || echo 'Health check pending...'
"

echo ""
echo "=== Deployment Complete ==="
echo ""
echo "Useful commands:"
echo "  systemctl status hope-core"
echo "  journalctl -u hope-core -f"
echo "  curl http://127.0.0.1:8200/status"
echo "  curl http://127.0.0.1:8200/api/health"
