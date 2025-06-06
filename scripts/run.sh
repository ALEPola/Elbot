#!/usr/bin/env bash
# Simple helper to launch Elbot from the project root.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

# Ensure the project is installed
if [ ! -d "$ROOT_DIR/.venv" ]; then
    "$SCRIPT_DIR/install.sh" --yes
fi

source "$ROOT_DIR/.venv/bin/activate"

python -m elbot.main "$@"

