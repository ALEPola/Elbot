#!/usr/bin/env bash
# Pull the latest version of Elbot from GitHub
set -euo pipefail

ENV_FILE="$(cd "$(dirname "$0")/.." && pwd)/.env"
BACKUP_FILE="$ENV_FILE.bak"

restore_env() {
    if [ -f "$BACKUP_FILE" ]; then
        mv "$BACKUP_FILE" "$ENV_FILE"
    fi
}

trap restore_env EXIT

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [ -f "$ENV_FILE" ]; then
    cp "$ENV_FILE" "$BACKUP_FILE"
fi

echo "[1/2] Pulling latest changes..."
git pull --ff-only

echo "[2/2] Updating Python dependencies..."
if [ -d ".venv" ]; then
    source .venv/bin/activate
    pip install --upgrade -e .
fi

