#!/bin/bash
set -euo pipefail

echo "🚀 Starting ELBOT deployment..."

# Parameterize branch name
BRANCH="Working"

# 0) Free up ports
echo "🔄 Clearing ports..."
sudo fuser -k 8080/tcp || true

# 1) Navigate to the bot's folder
cd /home/alex/ELBOT

# Backup current code and .env
BACKUP_DIR="/home/alex/ELBOT/backup_$(date +%Y%m%d_%H%M%S)"
echo "🗄️ Backing up current code and .env to $BACKUP_DIR..."
mkdir -p "$BACKUP_DIR"
rsync -av --exclude "backup_*" --exclude "venv" /home/alex/ELBOT/ "$BACKUP_DIR" --delete
cp /home/alex/ELBOT/.env "$BACKUP_DIR" 2>/dev/null || true

# Rotate logs
LOG_DIR="/var/log/elbot"
if [ ! -d "$LOG_DIR" ]; then
    echo "📝 Creating log directory: $LOG_DIR"
    mkdir -p "$LOG_DIR"
fi

# 2) Handle git changes
if [ -n "$(git status --porcelain)" ]; then
    echo "🔄 Stashing local changes..."
    git stash push -m "Auto stash before deploy $(date)"
fi

# 3) Pull latest changes
echo "⬇️ Pulling latest code from branch '$BRANCH'..."
git fetch origin "$BRANCH"
git checkout "$BRANCH"
git pull origin "$BRANCH"

# 4) Set up virtual environment
echo "🔧 Setting up virtual environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate

# 5) Install dependencies
echo "📦 Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt 2>&1 | grep -v ".pyc$" || true

# 6) Run tests
echo "🧪 Running tests..."
if ! python -m pytest --maxfail=1 --disable-warnings; then
    echo "❌ Error during deployment at step: Running tests"
    echo "🔄 Rolling back to previous state..."
    rsync -av --exclude "venv" "$BACKUP_DIR/" /home/alex/ELBOT/ --delete
    exit 1
fi

# 7) Stop services
echo "🛑 Stopping services..."
sudo systemctl stop elbot.service || true
sudo systemctl stop elbot-web.service || true

# 8) Update service files
echo "📄 Updating service files..."
sudo cp elbot.service /etc/systemd/system/
sudo cp elbot-web.service /etc/systemd/system/
sudo systemctl daemon-reload

# 9) Start services
echo "🚀 Starting services..."
sudo systemctl start elbot.service
sudo systemctl start elbot-web.service

# 10) Health check
echo "🔍 Performing health check..."
sleep 5  # Give services time to start

# Check bot service
if ! systemctl is-active --quiet elbot.service; then
    echo "❌ Bot service failed to start! Rolling back..."
    rsync -av --exclude "venv" "$BACKUP_DIR/" /home/alex/ELBOT/ --delete
    sudo systemctl restart elbot.service
    exit 1
fi

# Check web service
if ! systemctl is-active --quiet elbot-web.service; then
    echo "❌ Web service failed to start! Rolling back..."
    rsync -av --exclude "venv" "$BACKUP_DIR/" /home/alex/ELBOT/ --delete
    sudo systemctl restart elbot-web.service
    exit 1
fi

echo "✅ Deployment completed successfully."

