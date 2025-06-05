#!/bin/bash
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log(){ echo -e "${GREEN}$1${NC}"; }
warn(){ echo -e "${YELLOW}$1${NC}"; }
error(){ echo -e "${RED}$1${NC}"; exit 1; }

if [ "$EUID" -eq 0 ]; then
    error "Please do not run this script as root"
fi

cd "$(dirname "$0")"
REPO_DIR="$(pwd)"
USER_NAME="$(whoami)"

log "Checking dependencies..."
for cmd in python3 pip3 git; do
    command -v $cmd >/dev/null || error "$cmd is required"
done

SYSTEMCTL=$(command -v systemctl || true)

log "Preparing virtual environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
else
    warn "Virtualenv already exists"
fi

source venv/bin/activate
log "Installing Python packages..."
pip install --upgrade pip
pip install -r requirements.txt
deactivate

log "Ensuring .env file..."
if [ ! -f .env ]; then
    cp .env.example .env 2>/dev/null || touch .env
fi

if ! grep -q "^DISCORD_TOKEN=" .env; then
    read -rp "Enter your DISCORD_TOKEN: " TOKEN
    echo "DISCORD_TOKEN=$TOKEN" >> .env
fi

log "Generating service files..."
for svc in elbot.service elbot-web.service elbot-update.service; do
    template="$svc.template"
    if [ -f "$template" ]; then
        sed "s#__WORKDIR__#$REPO_DIR#g; s#__USER__#$USER_NAME#g" "$template" > "$svc"
    fi
done

if [ -n "$SYSTEMCTL" ]; then
    log "Installing systemd services..."
    sudo mkdir -p /var/log/elbot
    sudo chown "$USER_NAME" /var/log/elbot
    sudo cp elbot.service elbot-web.service elbot-update.service elbot-update.timer /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable elbot.service elbot-web.service elbot-update.timer
else
    warn "systemd not available; skipping service installation"
fi

log "Install complete"


