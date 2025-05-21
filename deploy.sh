#!/bin/bash
set -euo pipefail

# 0) Free up port 8080 so nothing is left hanging on your web panel
echo "🔄 Clearing port 8080…"
sudo fuser -k 8080/tcp || true

# 1) Navigate to the bot's folder
cd /home/alex/ELBOT

# 2) Check for unstaged changes and stash them automatically if any exist
if [ -n "$(git status --porcelain)" ]; then
    echo "🔄 Unstaged changes detected. Stashing changes..."
    git stash push -m "Auto stash before deploy"
fi

# 3) Pull the latest changes from the Working branch
echo "🔄 Pulling latest code from Working branch..."
git pull origin Working

# 4) Install or update dependencies
echo "📦 Installing/updating dependencies..."
pip install -r requirements.txt

# 5) Run unit tests
echo "🧪 Running unit tests..."
pytest --maxfail=1 --disable-warnings

# 6) Restart the bot service
echo "🔁 Restarting ELBOT service..."
sudo systemctl restart elbot.service

# 7) Check the status of the bot service
echo "✅ Checking bot service status..."
sudo systemctl status elbot.service --no-pager

echo "🎉 Deployment completed successfully!"

