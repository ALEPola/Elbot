#!/usr/bin/env bash
# Simple helper to launch Elbot from the project root.
set -e

if [ -d "$(dirname "$0")/../.venv" ]; then
    source "$(dirname "$0")/../.venv/bin/activate"
fi

python -m elbot.main "$@"

