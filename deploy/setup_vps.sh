#!/bin/bash
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29T12:30:00Z
# Purpose: HOPE VPS Initial Setup Script
# Security: Fail-closed, atomic operations, permission hardening
# === END SIGNATURE ===

set -euo pipefail
IFS=$'\n\t'

# === CONFIGURATION ===
HOPE_USER="hope"
HOPE_DIR="/opt/hope"
PYTHON_VERSION="3.11"
LOG_FILE="/var/log/hope-setup.log"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log() {
    local level="$1"; shift
    local msg="$*"
    local ts=$(date '+%Y-%m-%d %H:%M:%S')
    echo -e "${ts} [${level}] ${msg}" | tee -a "$LOG_FILE"
}

info() { log "INFO" "${CYAN}$*${NC}"; }
success() { log "OK" "${GREEN}$*${NC}"; }
warn() { log "WARN" "${YELLOW}$*${NC}"; }
error() { log "ERROR" "${RED}$*${NC}"; }
fatal() { error "$*"; exit 1; }

check_root() {
    if [[ $EUID -ne 0 ]]; then
        fatal "Must run as root (use sudo)"
    fi
}

# === STEP 1: Prerequisites ===
install_prerequisites() {
    info "Installing prerequisites..."

    apt-get update -qq
    apt-get install -y -qq \
        python${PYTHON_VERSION} \
        python${PYTHON_VERSION}-venv \
        python${PYTHON_VERSION}-dev \
        git curl jq htop rsync

    update-alternatives --install /usr/bin/python3 python3 /usr/bin/python${PYTHON_VERSION} 1 2>/dev/null || true

    success "Prerequisites installed"
}

# === STEP 2: User and Directories ===
create_user_and_dirs() {
    info "Creating user and directories..."

    # Create user if not exists
    if ! id "$HOPE_USER" &>/dev/null; then
        useradd -r -m -d "$HOPE_DIR" -s /bin/bash "$HOPE_USER"
        success "Created user: $HOPE_USER"
    else
        info "User $HOPE_USER exists"
    fi

    # Directory structure
    mkdir -p "$HOPE_DIR"/{minibot,secrets,venv,logs,backups}
    mkdir -p "$HOPE_DIR"/minibot/{state,logs}
    mkdir -p "$HOPE_DIR"/minibot/state/{evidence,pids,snapshots}

    # Ownership and permissions
    chown -R "$HOPE_USER:$HOPE_USER" "$HOPE_DIR"
    chmod 750 "$HOPE_DIR"
    chmod 700 "$HOPE_DIR/secrets"
    chmod 755 "$HOPE_DIR/minibot"

    success "Directories created: $HOPE_DIR"
}

# === STEP 3: Python venv ===
setup_venv() {
    info "Setting up Python venv..."

    local venv_dir="$HOPE_DIR/venv"

    if [[ ! -d "$venv_dir/bin" ]]; then
        sudo -u "$HOPE_USER" python${PYTHON_VERSION} -m venv "$venv_dir"
        success "Venv created"
    fi

    sudo -u "$HOPE_USER" "$venv_dir/bin/pip" install --upgrade pip setuptools wheel -q

    local req_file="$HOPE_DIR/minibot/requirements.txt"
    if [[ -f "$req_file" ]]; then
        sudo -u "$HOPE_USER" "$venv_dir/bin/pip" install -r "$req_file" -q
        success "Requirements installed"
    else
        warn "requirements.txt not found (will install after code sync)"
    fi
}

# === STEP 4: Systemd Services ===
install_systemd() {
    info "Installing systemd services..."

    local src="$HOPE_DIR/minibot/deploy/systemd"
    local dest="/etc/systemd/system"

    if [[ ! -d "$src" ]]; then
        warn "Systemd source not found, creating embedded services..."
        mkdir -p "$src"

        # hope-tgbot.service
        cat > "$src/hope-tgbot.service" << 'EOF'
[Unit]
Description=HOPE Telegram Bot
After=network-online.target
PartOf=hope-stack.target

[Service]
Type=notify
User=hope
Group=hope
WorkingDirectory=/opt/hope/minibot
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-/opt/hope/secrets/hope.env
ExecStart=/opt/hope/venv/bin/python -u tg_bot_simple.py
Restart=always
RestartSec=10
WatchdogSec=120
TimeoutStopSec=30
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/opt/hope/minibot/state /opt/hope/minibot/logs
StandardOutput=journal
StandardError=journal
SyslogIdentifier=hope-tgbot

[Install]
WantedBy=hope-stack.target
EOF

        # hope-core.service
        cat > "$src/hope-core.service" << 'EOF'
[Unit]
Description=HOPE Trade Core
After=network-online.target
PartOf=hope-stack.target
Before=hope-tgbot.service

[Service]
Type=notify
User=hope
Group=hope
WorkingDirectory=/opt/hope/minibot
Environment=PYTHONUNBUFFERED=1
Environment=HOPE_MODE=DRY
EnvironmentFile=-/opt/hope/secrets/hope.env
ExecStart=/opt/hope/venv/bin/python -u -m core.entrypoint --mode ${HOPE_MODE}
Restart=on-failure
RestartSec=30
WatchdogSec=120
TimeoutStopSec=120
KillMode=mixed
KillSignal=SIGTERM
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/opt/hope/minibot/state /opt/hope/minibot/logs
StandardOutput=journal
StandardError=journal
SyslogIdentifier=hope-core

[Install]
WantedBy=hope-stack.target
EOF

        # hope-stack.target
        cat > "$src/hope-stack.target" << 'EOF'
[Unit]
Description=HOPE Trading Stack
After=network-online.target
Wants=hope-core.service hope-tgbot.service

[Install]
WantedBy=multi-user.target
EOF

        chown -R "$HOPE_USER:$HOPE_USER" "$src"
    fi

    # Install services
    for f in "$src"/*.service "$src"/*.target; do
        [[ -f "$f" ]] || continue
        cp "$f" "$dest/$(basename "$f")"
        chmod 644 "$dest/$(basename "$f")"
        success "Installed: $(basename "$f")"
    done

    systemctl daemon-reload
    systemctl enable hope-stack.target hope-tgbot hope-core 2>/dev/null || true

    success "Systemd services installed"
}

# === STEP 5: Secrets Template ===
setup_secrets() {
    info "Setting up secrets..."

    local secrets_file="$HOPE_DIR/secrets/hope.env"

    if [[ -f "$secrets_file" ]]; then
        info "Secrets file exists"
        chmod 600 "$secrets_file"
    else
        cat > "$secrets_file" << 'EOF'
# HOPE Trading Bot Secrets
# SECURITY: chmod 600, owned by hope:hope
# DO NOT COMMIT TO GIT

# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_ADMIN_CHAT_ID=your_chat_id_here

# Binance MAINNET
BINANCE_API_KEY=your_api_key_here
BINANCE_API_SECRET=your_api_secret_here

# Binance TESTNET (recommended for testing)
BINANCE_TESTNET_API_KEY=your_testnet_key_here
BINANCE_TESTNET_API_SECRET=your_testnet_secret_here

# Trading Mode: DRY | TESTNET | MAINNET
# HOPE_MODE=DRY
EOF
        chmod 600 "$secrets_file"
        chown "$HOPE_USER:$HOPE_USER" "$secrets_file"
        warn "Created secrets template at: $secrets_file"
        warn "MANUAL ACTION: Edit with your real secrets!"
    fi
}

# === STEP 6: SSH Setup for hope user ===
setup_ssh() {
    info "Setting up SSH for hope user..."

    local ssh_dir="$HOPE_DIR/.ssh"

    mkdir -p "$ssh_dir"

    # Copy root's authorized_keys if exists
    if [[ -f /root/.ssh/authorized_keys ]]; then
        cp /root/.ssh/authorized_keys "$ssh_dir/"
        success "Copied authorized_keys to hope user"
    fi

    chown -R "$HOPE_USER:$HOPE_USER" "$ssh_dir"
    chmod 700 "$ssh_dir"
    [[ -f "$ssh_dir/authorized_keys" ]] && chmod 600 "$ssh_dir/authorized_keys"
}

# === STEP 7: Logrotate ===
setup_logrotate() {
    info "Setting up logrotate..."

    cat > /etc/logrotate.d/hope << EOF
$HOPE_DIR/minibot/logs/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    create 640 $HOPE_USER $HOPE_USER
}
EOF

    success "Logrotate configured"
}

# === STEP 8: Verify ===
verify_installation() {
    info "Verifying installation..."

    local errors=0

    id "$HOPE_USER" &>/dev/null && success "User: $HOPE_USER" || { error "User missing"; ((errors++)); }
    [[ -d "$HOPE_DIR/minibot" ]] && success "Dir: minibot" || { error "Dir missing"; ((errors++)); }
    [[ -x "$HOPE_DIR/venv/bin/python" ]] && success "Python: venv OK" || { error "Python missing"; ((errors++)); }
    systemctl list-unit-files | grep -q hope-tgbot && success "Service: hope-tgbot" || { error "Service missing"; ((errors++)); }

    if [[ $errors -eq 0 ]]; then
        success "All checks passed!"
        return 0
    else
        error "$errors error(s)"
        return 1
    fi
}

# === STEP 9: Print Usage ===
print_usage() {
    echo ""
    echo "========================================"
    echo "   HOPE VPS Setup Complete!"
    echo "========================================"
    echo ""
    echo "NEXT STEPS:"
    echo ""
    echo "1. Edit secrets:"
    echo "   nano $HOPE_DIR/secrets/hope.env"
    echo ""
    echo "2. Sync code from PC:"
    echo "   rsync -avz --exclude '.git' --exclude 'state/' \\"
    echo "     C:/Users/kirillDev/Desktop/TradingBot/minibot/ \\"
    echo "     hope@$(hostname -I | awk '{print $1}'):$HOPE_DIR/minibot/"
    echo ""
    echo "3. Install requirements:"
    echo "   sudo -u hope $HOPE_DIR/venv/bin/pip install -r $HOPE_DIR/minibot/requirements.txt"
    echo ""
    echo "4. Start services:"
    echo "   systemctl start hope-stack.target"
    echo ""
    echo "5. Check status:"
    echo "   systemctl status hope-core hope-tgbot"
    echo "   journalctl -u hope-tgbot -f"
    echo ""
    echo "TRADING MODES:"
    echo "  DRY      = Paper trading (safe)"
    echo "  TESTNET  = Binance testnet"
    echo "  MAINNET  = REAL MONEY!"
    echo ""
}

# === MAIN ===
main() {
    echo "========================================"
    echo "   HOPE Trading System - VPS Setup"
    echo "========================================"

    check_root

    mkdir -p "$(dirname "$LOG_FILE")"
    touch "$LOG_FILE"

    info "Starting setup (log: $LOG_FILE)"

    install_prerequisites
    create_user_and_dirs
    setup_venv
    setup_ssh
    install_systemd
    setup_secrets
    setup_logrotate
    verify_installation
    print_usage
}

main "$@"
