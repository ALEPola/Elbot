#!/bin/bash
set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

log(){ echo -e "${GREEN}$1${NC}"; }
err(){ echo -e "${RED}$1${NC}"; }

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR"

log "Activating virtualenv..."
source "$REPO_DIR/venv/bin/activate"

log "Starting ELBOT for testing..."
if ! python main.py; then
    err "Bot crashed with exit code $?"
fi

deactivate
