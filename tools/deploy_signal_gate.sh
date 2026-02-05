#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# HOPE SIGNAL GATE - DEPLOYMENT SCRIPT
# 2026-02-05
# ═══════════════════════════════════════════════════════════════════
# 
# Usage:
#   ./deploy_signal_gate.sh
#
# This script:
#   1. Backs up existing files
#   2. Copies new modules to /opt/hope/minibot/core/ai/
#   3. Tests imports
#   4. Patches autotrader (optional)
#   5. Restarts services
#
# ═══════════════════════════════════════════════════════════════════

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

HOPE_DIR="/opt/hope/minibot"
BACKUP_DIR="/opt/hope/backups/$(date +%Y%m%d_%H%M%S)"

echo "═══════════════════════════════════════════════════════════════════"
echo "  🚀 HOPE SIGNAL GATE DEPLOYMENT"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "═══════════════════════════════════════════════════════════════════"

# ─────────────────────────────────────────────────────────────────────
echo -e "\n${YELLOW}[1/5] Creating backup...${NC}"
mkdir -p "$BACKUP_DIR"
if [ -d "$HOPE_DIR/core/ai" ]; then
    cp -r "$HOPE_DIR/core/ai" "$BACKUP_DIR/" 2>/dev/null || true
fi
cp "$HOPE_DIR/scripts/autotrader.py" "$BACKUP_DIR/" 2>/dev/null || true
echo -e "${GREEN}✅ Backup created: $BACKUP_DIR${NC}"

# ─────────────────────────────────────────────────────────────────────
echo -e "\n${YELLOW}[2/5] Creating directories...${NC}"
mkdir -p "$HOPE_DIR/core/ai"
echo -e "${GREEN}✅ Directories ready${NC}"

# ─────────────────────────────────────────────────────────────────────
echo -e "\n${YELLOW}[3/5] Copying modules...${NC}"

# Определить директорию скрипта
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Копировать файлы
cp "$SCRIPT_DIR/core/ai/anti_chase_filter.py" "$HOPE_DIR/core/ai/"
cp "$SCRIPT_DIR/core/ai/adaptive_confidence.py" "$HOPE_DIR/core/ai/"
cp "$SCRIPT_DIR/core/ai/time_based_exit.py" "$HOPE_DIR/core/ai/"
cp "$SCRIPT_DIR/core/ai/__init__.py" "$HOPE_DIR/core/ai/"

# Убедиться что core/__init__.py существует
if [ ! -f "$HOPE_DIR/core/__init__.py" ]; then
    echo '"""HOPE Core modules"""' > "$HOPE_DIR/core/__init__.py"
fi

echo -e "${GREEN}✅ Modules copied${NC}"

# ─────────────────────────────────────────────────────────────────────
echo -e "\n${YELLOW}[4/5] Testing imports...${NC}"

cd "$HOPE_DIR"
python3 -c "
from core.ai import SignalGate, AntiChaseFilter, ObservationMode
from core.ai import AdaptiveConfidence
from core.ai import TimeBasedExitRules

print('✅ SignalGate imported')
print('✅ AdaptiveConfidence imported')
print('✅ TimeBasedExitRules imported')

# Quick test
gate = SignalGate()
print(f'✅ SignalGate initialized: {gate.get_status()}')
"

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ All imports successful${NC}"
else
    echo -e "${RED}❌ Import test failed!${NC}"
    exit 1
fi

# ─────────────────────────────────────────────────────────────────────
echo -e "\n${YELLOW}[5/5] Checking autotrader...${NC}"

# Проверить есть ли уже интеграция
if grep -q "SignalGate" "$HOPE_DIR/scripts/autotrader.py" 2>/dev/null; then
    echo -e "${GREEN}✅ SignalGate already integrated in autotrader${NC}"
else
    echo -e "${YELLOW}⚠️ SignalGate NOT integrated in autotrader${NC}"
    echo ""
    echo "To integrate, run:"
    echo "  python3 $SCRIPT_DIR/patch_autotrader_gates.py $HOPE_DIR/scripts/autotrader.py"
    echo ""
    echo "Or manually add the check before opening positions (see INTEGRATION.md)"
fi

# ─────────────────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════════════"
echo -e "  ${GREEN}✅ DEPLOYMENT COMPLETE${NC}"
echo "═══════════════════════════════════════════════════════════════════"
echo ""
echo "Modules installed:"
echo "  - AntiChaseFilter: blocks entry if price moved >1.5% in 3min"
echo "  - ObservationMode: stops trading if WR < 35%"
echo "  - AdaptiveConfidence: dynamic confidence threshold"
echo "  - TimeBasedExit: quick loss + stale position rules"
echo ""
echo "Next steps:"
echo "  1. Integrate SignalGate in autotrader (if not done)"
echo "  2. Restart services: systemctl restart hope-autotrader"
echo "  3. Monitor logs: journalctl -u hope-autotrader -f"
echo ""
echo "Backup location: $BACKUP_DIR"
echo "═══════════════════════════════════════════════════════════════════"
