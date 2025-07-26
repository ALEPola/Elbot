#!/usr/bin/env bash
# Simple helper to launch Elbot from the project root.
set -euo pipefail

START_LAVALINK=0

if [ "${1:-}" = "--with-lavalink" ]; then
    START_LAVALINK=1
    shift
fi

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

if [ "$START_LAVALINK" -eq 1 ]; then
    "$SCRIPT_DIR/lavalink.sh" start &
    LAVALINK_PID=$!
fi

python -m elbot.main "$@"

if [ "$START_LAVALINK" -eq 1 ]; then
    "$SCRIPT_DIR/lavalink.sh" stop >/dev/null 2>&1 || true
    if [ -n "$LAVALINK_PID" ]; then
        kill "$LAVALINK_PID" >/dev/null 2>&1 || true
    fi
fi

