#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -d ".venv" ]; then python3 -m venv .venv; fi
. .venv/bin/activate
python -m pip install -U pip wheel
pip install -r requirements.txt
cp -n .env.example .env 2>/dev/null || true
echo ">> Edit .env to set DISCORD_TOKEN if you haven't."
python -m elbot.main
