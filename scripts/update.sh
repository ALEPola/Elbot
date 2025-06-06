#!/usr/bin/env bash
# Pull the latest version of Elbot from GitHub
set -e

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

echo "Updating Elbot..."
git pull --ff-only

