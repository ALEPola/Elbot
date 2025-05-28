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

# Install Python dependencies for the web portal
if [ -f "requirements.txt" ]; then
    echo "📦 Installing Python dependencies..."
    source venv/bin/activate
    pip install -r requirements.txt
    deactivate
fi

# Build and deploy the React frontend
if [ -d "web" ]; then
    echo "🌐 Setting up the web portal..."
    cd web
    if [ -d "elbot-frontend" ]; then
        cd elbot-frontend
        npm install --legacy-peer-deps
        npm run build
        cd ..
    else
        echo "❌ React frontend directory not found. Skipping frontend setup."
    fi
    cd ..
fi

# Restart the web service
echo "🔄 Restarting ELBOT web service..."
sudo systemctl restart elbot-web.service

echo "🚀 Restarting ELBOT service..."
sudo systemctl restart elbot.service

echo "✅ Deployment complete. ELBOT restarted from '$BRANCH'."



