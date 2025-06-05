#!/bin/bash
set -euo pipefail

BRANCH="${1:-main}"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

log(){ echo -e "${GREEN}$1${NC}"; }
err(){ echo -e "${RED}$1${NC}"; exit 1; }

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR"

log "Fetching updates for branch '$BRANCH'..."
if ! git fetch origin "$BRANCH"; then err "git fetch failed"; fi
if ! git checkout "$BRANCH"; then err "git checkout failed"; fi
if ! git pull origin "$BRANCH"; then err "git pull failed"; fi

log "Activating virtualenv..."
source "$REPO_DIR/venv/bin/activate"

log "Installing dependencies..."
if ! pip install -r requirements.txt; then
    deactivate
    err "pip install failed"
fi

deactivate

if command -v systemctl >/dev/null; then
    log "Restarting elbot.service..."
    if ! sudo systemctl restart elbot.service; then err "Failed to restart service"; fi
else
    log "systemd not found, skipping service restart"
fi

log "Deployment complete"
