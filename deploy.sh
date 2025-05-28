#!/bin/bash
set -euo pipefail

# 0) Free up port 8080 so nothing is left hanging on your web panel
echo "🔄 Clearing port 8080…"
sudo fuser -k 8080/tcp || true

# 1) Navigate to the bot's folder
cd /home/alex/ELBOT

# Backup current code and .env before pulling new changes
BACKUP_DIR="/home/alex/ELBOT/backup_$(date +%Y%m%d_%H%M%S)"
echo "🗄️ Backing up current code and .env to $BACKUP_DIR..."
mkdir -p "$BACKUP_DIR"
rsync -av --exclude "backup_*" /home/alex/ELBOT/ "$BACKUP_DIR" --delete
cp /home/alex/ELBOT/.env "$BACKUP_DIR" 2>/dev/null || true

# Rotate logs: keep only the last 5 log files
LOG_DIR="/var/log/elbot"
if [ -d "$LOG_DIR" ]; then
  ls -1t $LOG_DIR/*.log 2>/dev/null | tail -n +6 | xargs rm -f
fi

# 2) Check for unstaged changes and stash them automatically if any exist
if [ -n "$(git status --porcelain)" ]; then
    echo "🔄 Unstaged changes detected. Stashing changes..."
    git stash push -m "Auto stash before deploy"
fi

# 3) Pull the latest changes from the Working branch
echo "🔄 Pulling latest code from Working branch..."
git pull origin Working

# 4) Set up and activate the virtual environment
echo "📦 Setting up virtual environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate

# 5) Install or update dependencies
echo "📦 Installing/updating dependencies..."
# Suppress .pyc output from pip install
pip install -r requirements.txt 2>&1 | grep -v ".pyc$" || true

# 6) Run unit tests
echo "🧪 Running unit tests..."
pytest --maxfail=1 --disable-warnings

# 6.5) Kill any standalone bot processes
pkill -f main.py || true

# 7) Restart the bot service
echo "🔁 Restarting ELBOT service..."
sudo systemctl restart elbot.service

# 8) Check the status of the bot service
echo "✅ Checking bot service status..."
sudo systemctl status elbot.service --no-pager

# Health check after restart
sleep 5
echo "🔎 Performing health check..."
if systemctl is-active --quiet elbot.service; then
  echo "✅ ELBOT service is running."
else
  echo "❌ ELBOT service failed to start! Rolling back..."
  # Rollback to previous backup
  cp -r "$BACKUP_DIR"/* /home/alex/ELBOT/
  sudo systemctl restart elbot.service
  echo "🔁 Rolled back to previous working state."
fi

echo "🎉 Deployment completed successfully!"

# Pi deployment steps (streamlined)
echo "📡 Pi deployment steps completed!"

