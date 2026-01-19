#!/bin/bash
# ============================================================
# HOPE VPS: Pull latest code and restart services
#
# Usage (on VPS):
#   cd /opt/hope/minibot && ./scripts/vps_pull_deploy.sh
#
# Or from any device via SSH:
#   ssh root@46.62.232.161 "cd /opt/hope/minibot && ./scripts/vps_pull_deploy.sh"
# ============================================================

set -e

MINIBOT_DIR="/opt/hope/minibot"
cd "$MINIBOT_DIR"

echo "============================================================"
echo "  HOPE VPS Deploy"
echo "============================================================"
echo ""

# Pull latest code
echo "[1/4] Pulling latest code..."
git fetch origin
git reset --hard origin/main
echo "Commit: $(git log -1 --oneline)"
echo ""

# Verify syntax
echo "[2/4] Verifying Python syntax..."
python3 -m py_compile core/friend_bridge_server.py
python3 -m py_compile core/gpt_bridge_runner.py
python3 -m py_compile core/chat_dispatch.py
echo "Syntax OK"
echo ""

# Restart services
echo "[3/4] Restarting services..."
systemctl restart friend-bridge gpt-bridge-runner
sleep 2

# Verify status
echo "[4/4] Service status..."
systemctl status friend-bridge gpt-bridge-runner --no-pager

echo ""
echo "============================================================"
echo "  Deploy complete!"
echo ""
echo "  Verify with:"
echo "    journalctl -u friend-bridge -u gpt-bridge-runner --since '2 min ago'"
echo "============================================================"
