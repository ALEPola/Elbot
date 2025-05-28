#!/bin/bash

set -e  # Stop on error

BOT_DIR="/home/alex/ELBOT"
BACKUP_DIR="$BOT_DIR/backup_$(date +%Y%m%d_%H%M%S)"
BRANCH="Working"

cd "$BOT_DIR"

echo "📦 Backing up current state to $BACKUP_DIR..."
rsync -av --exclude "backup_*" --exclude ".git" --exclude "venv" --exclude ".venv" "$BOT_DIR/" "$BACKUP_DIR"

echo "🔁 Checking if git repo is healthy..."
if [ ! -d ".git" ]; then
    echo "❌ .git directory missing. Restore from backup manually. Aborting."
    exit 1
fi

echo "🔄 Fetching and resetting to latest from '$BRANCH'..."
git fetch origin "$BRANCH"
git reset --hard "origin/$BRANCH"

echo "🚀 Restarting ELBOT service..."
sudo systemctl restart elbot.service

echo "✅ Deployment complete. ELBOT restarted from '$BRANCH'."



