#!/usr/bin/env bash
# Pull the latest version of Elbot from GitHub
set -e

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

echo "[1/2] Pulling latest changes..."
git pull --ff-only

echo "[2/2] Updating Python dependencies..."
if [ -d ".venv" ]; then
    source .venv/bin/activate
    pip install --upgrade -e .
fi

