#!/bin/bash
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29T00:30:00Z
# Purpose: HOPE VPS Deployment Script - PC to Linux VPS
# Security: Fail-closed, no secrets in git, atomic deployments
# === END SIGNATURE ===
#
# HOPE VPS Deployment Script
#
# Deploys HOPE trading system from Windows PC to Linux VPS.
# Handles: code sync, venv setup, systemd services, secrets.
#
# Usage:
#   ./deploy_to_vps.sh [--init|--update|--restart|--status|--logs]
#
# Prerequisites:
#   - SSH key configured for VPS access
#   - rsync installed on both sides
#   - VPS user 'hope' created with sudo access
#
# VPS Structure:
#   /opt/hope/minibot/  - Application code
#   /opt/hope/venv/     - Python virtual environment
#   /etc/hope/.env      - Secrets (manual setup once)
#   /var/log/hope/      - Log files
#

set -euo pipefail

# === CONFIGURATION ===
VPS_HOST="${VPS_HOST:-46.62.232.161}"
VPS_USER="${VPS_USER:-hope}"
VPS_PORT="${VPS_PORT:-22}"

# Paths
LOCAL_PROJECT="${LOCAL_PROJECT:-$(dirname "$(dirname "$(readlink -f "$0")")")}"
REMOTE_BASE="/opt/hope"
REMOTE_PROJECT="${REMOTE_BASE}/minibot"
REMOTE_VENV="${REMOTE_BASE}/venv"
REMOTE_SECRETS="/etc/hope"

# SSH options
SSH_OPTS="-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"
SSH_CMD="ssh ${SSH_OPTS} -p ${VPS_PORT} ${VPS_USER}@${VPS_HOST}"
RSYNC_OPTS="-avz --delete --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' --exclude='.env' --exclude='state/' --exclude='*.tmp' --exclude='*.log'"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# === FUNCTIONS ===

check_prerequisites() {
    log_info "Checking prerequisites..."

    # Check rsync
    command -v rsync &>/dev/null || log_error "rsync not found. Install with: apt install rsync"

    # Check SSH connectivity
    ${SSH_CMD} "echo 'SSH OK'" &>/dev/null || log_error "Cannot connect to VPS. Check SSH config."

    # Check local project exists
    [[ -d "${LOCAL_PROJECT}" ]] || log_error "Local project not found: ${LOCAL_PROJECT}"
    [[ -f "${LOCAL_PROJECT}/core/entrypoint.py" ]] || log_error "Invalid project structure"

    log_info "Prerequisites OK"
}

init_vps() {
    log_info "Initializing VPS structure..."

    ${SSH_CMD} << 'INIT_SCRIPT'
set -euo pipefail

# Create user if not exists
if ! id -u hope &>/dev/null; then
    sudo useradd -m -s /bin/bash hope
    sudo usermod -aG sudo hope
    echo "User 'hope' created"
fi

# Create directories
sudo mkdir -p /opt/hope/minibot
sudo mkdir -p /opt/hope/venv
sudo mkdir -p /etc/hope
sudo mkdir -p /var/log/hope

# Set ownership
sudo chown -R hope:hope /opt/hope
sudo chown -R hope:hope /var/log/hope
sudo chown hope:hope /etc/hope
sudo chmod 700 /etc/hope  # Secrets directory - restricted

# Create state directory
mkdir -p /opt/hope/minibot/state
mkdir -p /opt/hope/minibot/state/evidence
mkdir -p /opt/hope/minibot/state/snapshots

# Install Python venv
if [[ ! -f /opt/hope/venv/bin/python ]]; then
    python3 -m venv /opt/hope/venv
    /opt/hope/venv/bin/pip install --upgrade pip wheel
fi

echo "VPS structure initialized"
INIT_SCRIPT

    log_info "VPS initialized"
}

sync_code() {
    log_info "Syncing code to VPS..."

    rsync ${RSYNC_OPTS} \
        -e "ssh ${SSH_OPTS} -p ${VPS_PORT}" \
        "${LOCAL_PROJECT}/" \
        "${VPS_USER}@${VPS_HOST}:${REMOTE_PROJECT}/"

    log_info "Code synced"
}

install_deps() {
    log_info "Installing Python dependencies..."

    ${SSH_CMD} << 'DEPS_SCRIPT'
set -euo pipefail
cd /opt/hope/minibot

# Install requirements
if [[ -f requirements.txt ]]; then
    /opt/hope/venv/bin/pip install -r requirements.txt
fi

# Verify critical imports
/opt/hope/venv/bin/python -c "
import sys
sys.path.insert(0, '/opt/hope/minibot')
from core.entrypoint import main
print('Core imports OK')
"

echo "Dependencies installed"
DEPS_SCRIPT

    log_info "Dependencies installed"
}

install_services() {
    log_info "Installing systemd services..."

    ${SSH_CMD} << 'SERVICES_SCRIPT'
set -euo pipefail

# Copy service files
sudo cp /opt/hope/minibot/deploy/systemd/hope-core.service /etc/systemd/system/
sudo cp /opt/hope/minibot/deploy/systemd/hope-tgbot.service /etc/systemd/system/
sudo cp /opt/hope/minibot/deploy/systemd/hope-stack.target /etc/systemd/system/

# Reload systemd
sudo systemctl daemon-reload

# Enable services (but don't start yet)
sudo systemctl enable hope-core
sudo systemctl enable hope-tgbot
sudo systemctl enable hope-stack.target

echo "Services installed"
SERVICES_SCRIPT

    log_info "Services installed"
}

setup_secrets_placeholder() {
    log_info "Setting up secrets placeholder..."

    ${SSH_CMD} << 'SECRETS_SCRIPT'
set -euo pipefail

ENV_FILE="/etc/hope/.env"

if [[ ! -f "${ENV_FILE}" ]]; then
    cat > "${ENV_FILE}" << 'ENV_TEMPLATE'
# HOPE Trading Bot - Secrets Configuration
# WARNING: This file contains sensitive data. Never commit to git.
#
# Required keys:
# TELEGRAM_BOT_TOKEN=your_bot_token_here
# TELEGRAM_ADMIN_IDS=123456789
#
# Binance (choose one set):
# BINANCE_MAINNET_API_KEY=
# BINANCE_MAINNET_API_SECRET=
# BINANCE_TESTNET_API_KEY=
# BINANCE_TESTNET_API_SECRET=
#
# Trading mode (DRY/TESTNET/MAINNET):
# HOPE_MODE=DRY
# HOPE_SYMBOL=BTCUSDT
# HOPE_AMOUNT=11
ENV_TEMPLATE
    sudo chown hope:hope "${ENV_FILE}"
    sudo chmod 600 "${ENV_FILE}"
    echo "Secrets placeholder created at ${ENV_FILE}"
    echo "IMPORTANT: Edit this file manually with your real secrets!"
else
    echo "Secrets file already exists: ${ENV_FILE}"
fi
SECRETS_SCRIPT

    log_warn "Remember to edit /etc/hope/.env on VPS with real secrets!"
}

start_services() {
    log_info "Starting services..."

    ${SSH_CMD} << 'START_SCRIPT'
set -euo pipefail

# Check secrets exist
if ! grep -q "TELEGRAM_BOT_TOKEN=" /etc/hope/.env 2>/dev/null || \
   grep -q "TELEGRAM_BOT_TOKEN=$" /etc/hope/.env 2>/dev/null; then
    echo "ERROR: TELEGRAM_BOT_TOKEN not configured in /etc/hope/.env"
    exit 1
fi

# Start services
sudo systemctl start hope-core
sleep 2
sudo systemctl start hope-tgbot

# Check status
systemctl status hope-core --no-pager -l || true
systemctl status hope-tgbot --no-pager -l || true

echo "Services started"
START_SCRIPT

    log_info "Services started"
}

stop_services() {
    log_info "Stopping services..."

    ${SSH_CMD} << 'STOP_SCRIPT'
sudo systemctl stop hope-tgbot || true
sudo systemctl stop hope-core || true
echo "Services stopped"
STOP_SCRIPT

    log_info "Services stopped"
}

restart_services() {
    log_info "Restarting services..."
    stop_services
    sleep 2
    start_services
}

show_status() {
    log_info "Service status:"

    ${SSH_CMD} << 'STATUS_SCRIPT'
echo "=== HOPE Core ==="
systemctl status hope-core --no-pager -l 2>/dev/null || echo "Not running"
echo ""
echo "=== HOPE TgBot ==="
systemctl status hope-tgbot --no-pager -l 2>/dev/null || echo "Not running"
echo ""
echo "=== Health Files ==="
ls -la /opt/hope/minibot/state/health*.json 2>/dev/null || echo "No health files"
cat /opt/hope/minibot/state/health_v5.json 2>/dev/null || true
STATUS_SCRIPT
}

show_logs() {
    local lines="${1:-50}"
    log_info "Recent logs (last ${lines} lines):"

    ${SSH_CMD} "journalctl -u hope-core -u hope-tgbot -n ${lines} --no-pager"
}

full_deploy() {
    check_prerequisites
    stop_services 2>/dev/null || true
    sync_code
    install_deps
    install_services
    start_services
    show_status
}

# === MAIN ===

case "${1:-update}" in
    --init)
        check_prerequisites
        init_vps
        sync_code
        install_deps
        install_services
        setup_secrets_placeholder
        log_info "Init complete. Edit /etc/hope/.env then run: $0 --start"
        ;;
    --update)
        full_deploy
        ;;
    --sync)
        check_prerequisites
        sync_code
        ;;
    --restart)
        restart_services
        ;;
    --start)
        start_services
        ;;
    --stop)
        stop_services
        ;;
    --status)
        show_status
        ;;
    --logs)
        show_logs "${2:-50}"
        ;;
    --help)
        echo "HOPE VPS Deployment Script"
        echo ""
        echo "Usage: $0 [command]"
        echo ""
        echo "Commands:"
        echo "  --init      First-time VPS setup (creates user, dirs, venv)"
        echo "  --update    Full deploy: sync + deps + restart (default)"
        echo "  --sync      Sync code only (no restart)"
        echo "  --restart   Restart services"
        echo "  --start     Start services"
        echo "  --stop      Stop services"
        echo "  --status    Show service status"
        echo "  --logs [n]  Show last n log lines (default: 50)"
        echo ""
        echo "Environment:"
        echo "  VPS_HOST    Target host (default: 46.62.232.161)"
        echo "  VPS_USER    SSH user (default: hope)"
        echo "  VPS_PORT    SSH port (default: 22)"
        ;;
    *)
        log_error "Unknown command: $1. Use --help for usage."
        ;;
esac
