#!/bin/bash
# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-02-05T09:30:00Z
# Purpose: Deploy HOPE updates from GitHub to VPS
# === END SIGNATURE ===

# HOPE VPS Deploy Script
# Run this on VPS: bash deploy_vps.sh

set -e

echo "=== HOPE VPS Deploy ==="
echo "Date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

cd /opt/hope/minibot

# 1. Pull latest from GitHub
echo ""
echo "[1/5] Pulling from GitHub..."
git fetch origin
git pull origin master --ff-only

# 2. Verify syntax
echo ""
echo "[2/5] Verifying Python syntax..."
python3 -m py_compile hope_core/hope_core.py
python3 -m py_compile hope_core/ai_integration.py
python3 -m py_compile hope_core/journal/event_journal.py
python3 -m py_compile core/anti_chase_filter.py
python3 -m py_compile core/ai_trade_learner.py
echo "Syntax OK"

# 3. Restart HOPE Core service
echo ""
echo "[3/5] Restarting hope-core service..."
sudo systemctl restart hope-core || echo "Warning: hope-core service restart failed"

# 4. Restart autotrader (legacy)
echo ""
echo "[4/5] Restarting autotrader service..."
sudo systemctl restart hope-autotrader || echo "Warning: autotrader service restart failed"

# 5. Check status
echo ""
echo "[5/5] Checking service status..."
echo "--- hope-core ---"
sudo systemctl status hope-core --no-pager -l | head -15 || true
echo ""
echo "--- autotrader ---"
sudo systemctl status hope-autotrader --no-pager -l | head -15 || true

echo ""
echo "=== Deploy Complete ==="
echo ""
echo "Verify AI Gate is loaded:"
echo "  curl http://localhost:8201/health | jq '.ai_gate'"
echo ""
echo "Check logs:"
echo "  journalctl -u hope-core -f --since='5 min ago'"
