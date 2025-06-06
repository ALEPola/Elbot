#!/usr/bin/env bash
# Simple helper to launch Elbot from the project root.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

# Ensure the project is installed
if [ ! -d "$ROOT_DIR/.venv" ]; then
    "$SCRIPT_DIR/install.sh" --yes
fi

# Create a default .env if missing
if [ ! -f "$ROOT_DIR/.env" ] && [ -f "$ROOT_DIR/.env.example" ]; then
    cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
    echo "Created $ROOT_DIR/.env from template. Please edit it with your tokens."
fi

source "$ROOT_DIR/.venv/bin/activate"

python -m elbot.main "$@"

