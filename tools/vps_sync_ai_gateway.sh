#!/bin/bash
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-02-03T17:00:00Z
# Purpose: Sync AI Gateway files to VPS
# === END SIGNATURE ===

# Run this ON YOUR LOCAL MACHINE (Windows with Git Bash or WSL)
# Usage: ./vps_sync_ai_gateway.sh

VPS="hope@46.62.232.161"
SSH_KEY="~/.ssh/id_ed25519_hope"
REMOTE_DIR="/opt/hope/minibot/ai_gateway"
LOCAL_DIR="$(dirname "$0")/../ai_gateway"

echo "=== HOPE AI Gateway VPS Sync ==="
echo "VPS: $VPS"
echo "Key: $SSH_KEY"

# Copy files
echo ""
echo "Syncing contracts.py..."
scp -i "$SSH_KEY" "$LOCAL_DIR/contracts.py" "$VPS:$REMOTE_DIR/"

echo "Syncing status_manager.py..."
scp -i "$SSH_KEY" "$LOCAL_DIR/status_manager.py" "$VPS:$REMOTE_DIR/"

# Restart service
echo ""
echo "Restarting hope-ai-gateway service..."
ssh -i "$SSH_KEY" "$VPS" "sudo systemctl restart hope-ai-gateway"

# Check status
echo ""
echo "Checking service status..."
ssh -i "$SSH_KEY" "$VPS" "systemctl is-active hope-ai-gateway && curl -s http://127.0.0.1:8100/health"

echo ""
echo "=== Done ==="
