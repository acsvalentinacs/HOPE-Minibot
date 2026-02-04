#!/bin/bash
# === AI SIGNATURE ===
# Script: hope_core/deploy/integrate_hope_core.sh
# Created by: Claude (opus-4.5)
# Purpose: Integrate HOPE Core v2.0 with existing HOPE project on VPS
# === END SIGNATURE ===

set -e

echo "============================================================"
echo "    HOPE Core v2.0 - Integration Script"
echo "============================================================"
echo ""

# Configuration
VPS_HOST="${VPS_HOST:-46.62.232.161}"
VPS_USER="${VPS_USER:-root}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519_hope}"
REMOTE_DIR="/opt/hope/minibot"

SSH_CMD="ssh -i $SSH_KEY ${VPS_USER}@${VPS_HOST}"
SCP_CMD="scp -i $SSH_KEY"

# Check SSH key
if [ ! -f "$SSH_KEY" ]; then
    echo "ERROR: SSH key not found: $SSH_KEY"
    echo "Trying alternate location..."
    SSH_KEY="$HOME/.ssh/id_ed25519_hope"
    if [ ! -f "$SSH_KEY" ]; then
        echo "ERROR: SSH key not found. Set SSH_KEY environment variable."
        exit 1
    fi
fi

echo "Target: ${VPS_USER}@${VPS_HOST}"
echo "Remote: ${REMOTE_DIR}"
echo ""

# Step 1: Check VPS connectivity
echo "[1/6] Checking VPS connectivity..."
$SSH_CMD "echo 'VPS connection OK'" || {
    echo "ERROR: Cannot connect to VPS"
    exit 1
}

# Step 2: Backup existing services
echo "[2/6] Stopping existing services and backing up..."
$SSH_CMD "
    systemctl stop hope-autotrader hope-signal-loop hope-watchdog 2>/dev/null || true
    
    # Backup existing files
    BACKUP_DIR=${REMOTE_DIR}/backups/\$(date +%Y%m%d_%H%M%S)
    mkdir -p \$BACKUP_DIR
    
    # Backup autotrader if exists
    if [ -f ${REMOTE_DIR}/scripts/autotrader.py ]; then
        cp ${REMOTE_DIR}/scripts/autotrader.py \$BACKUP_DIR/
        echo 'Backed up autotrader.py'
    fi
    
    echo 'Backup completed: '\$BACKUP_DIR
"

# Step 3: Copy HOPE Core files
echo "[3/6] Copying HOPE Core files..."
LOCAL_DIR="$(dirname "$0")/.."

# Create hope_core directory
$SSH_CMD "mkdir -p ${REMOTE_DIR}/hope_core/{bus,state,journal,guardian,deploy}"

# Copy Python files
for file in hope_core.py api_server.py integration_bridge.py mocks.py alerts.py; do
    if [ -f "$LOCAL_DIR/$file" ]; then
        $SCP_CMD "$LOCAL_DIR/$file" "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/hope_core/"
        echo "  Copied: $file"
    fi
done

# Copy bus module
$SCP_CMD "$LOCAL_DIR/bus/"*.py "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/hope_core/bus/" 2>/dev/null || true
echo "  Copied: bus/"

# Copy state module
$SCP_CMD "$LOCAL_DIR/state/"*.py "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/hope_core/state/" 2>/dev/null || true
echo "  Copied: state/"

# Copy journal module
$SCP_CMD "$LOCAL_DIR/journal/"*.py "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/hope_core/journal/" 2>/dev/null || true
echo "  Copied: journal/"

# Copy guardian module
$SCP_CMD "$LOCAL_DIR/guardian/"*.py "${VPS_USER}@${VPS_HOST}:${REMOTE_DIR}/hope_core/guardian/" 2>/dev/null || true
echo "  Copied: guardian/"

# Create __init__.py files
$SSH_CMD "
    touch ${REMOTE_DIR}/hope_core/__init__.py
    touch ${REMOTE_DIR}/hope_core/bus/__init__.py
    touch ${REMOTE_DIR}/hope_core/state/__init__.py
    touch ${REMOTE_DIR}/hope_core/journal/__init__.py
    touch ${REMOTE_DIR}/hope_core/guardian/__init__.py
"

# Step 4: Create integration symlinks
echo "[4/6] Creating integration symlinks..."
$SSH_CMD "
    cd ${REMOTE_DIR}/hope_core
    
    # Link to existing modules
    ln -sf ../scripts/eye_of_god_v3.py eye_of_god_v3.py 2>/dev/null || true
    ln -sf ../scripts/order_executor.py order_executor.py 2>/dev/null || true
    
    echo 'Symlinks created'
"

# Step 5: Install/update dependencies
echo "[5/6] Installing dependencies..."
$SSH_CMD "
    cd ${REMOTE_DIR}
    source /opt/hope/venv/bin/activate 2>/dev/null || source .venv/bin/activate 2>/dev/null || true
    pip install fastapi uvicorn aiohttp psutil jsonschema --quiet 2>/dev/null || \
    pip install fastapi uvicorn aiohttp psutil jsonschema --break-system-packages --quiet
    echo 'Dependencies installed'
"

# Step 6: Test imports
echo "[6/6] Testing imports..."
$SSH_CMD "
    cd ${REMOTE_DIR}
    export PYTHONPATH=${REMOTE_DIR}:${REMOTE_DIR}/scripts:\$PYTHONPATH
    
    python3 -c '
import sys
sys.path.insert(0, \".\")
sys.path.insert(0, \"scripts\")
sys.path.insert(0, \"hope_core\")

print(\"Testing imports...\")

# Test HOPE Core
from hope_core.hope_core import HopeCore, HopeCoreConfig
print(\"✅ hope_core.hope_core\")

# Test existing modules
try:
    from eye_of_god_v3 import EyeOfGodV3
    print(\"✅ eye_of_god_v3 (REAL)\")
except ImportError as e:
    print(f\"⚠️  eye_of_god_v3: {e}\")

try:
    from order_executor import OrderExecutor
    print(\"✅ order_executor (REAL)\")
except ImportError as e:
    print(f\"⚠️  order_executor: {e}\")

print()
print(\"Integration check complete!\")
'
"

echo ""
echo "============================================================"
echo "    Integration Complete!"
echo "============================================================"
echo ""
echo "Next steps:"
echo "  1. Update systemd services:"
echo "     scp deploy/hope-core.service root@${VPS_HOST}:/etc/systemd/system/"
echo "     ssh root@${VPS_HOST} 'systemctl daemon-reload'"
echo ""
echo "  2. Start HOPE Core:"
echo "     ssh root@${VPS_HOST} 'systemctl start hope-core'"
echo ""
echo "  3. Check status:"
echo "     ssh root@${VPS_HOST} 'curl http://127.0.0.1:8200/api/health'"
echo ""
echo "  4. View dashboard:"
echo "     ssh root@${VPS_HOST} 'curl http://127.0.0.1:8200/dashboard'"
