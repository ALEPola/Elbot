#!/bin/bash
set -euo pipefail

echo "🚀 Starting ELBOT deployment..."

BRANCH="Working"
BOT_DIR="/home/alex/ELBOT"
BACKUP_DIR="$BOT_DIR/backup_$(date +%Y%m%d_%H%M%S)"
LOG_DIR="/var/log/elbot"

# 0) Free up web port
echo "🔄 Freeing up port 8080..."
sudo fuser -k 8080/tcp || true

# 1) Make backup
echo "🗄️ Backing up to $BACKUP_DIR..."
mkdir -p "$BACKUP_DIR"
rsync -av --exclude "backup_*" --exclude "venv" "$BOT_DIR/" "$BACKUP_DIR"

# 2) Validate Git repo
cd "$BOT_DIR"
if ! git rev-parse --is-inside-work-tree > /dev/null 2>&1; then
    echo "❌ Not a valid Git repository."
    exit 1
fi

# 3) Stash changes safely
if [[ -n "$(git status --porcelain)" ]]; then
    echo "🔄 Stashing local changes..."
    git stash push -m "Pre-deploy stash $(date)" || echo "⚠️ Nothing to stash."
fi

# 4) Checkout branch safely
git fetch origin "$BRANCH"
git checkout "$BRANCH" || {
    echo "❌ Failed to checkout $BRANCH"
    exit 1
}

# 5) Pull updates
echo "⬇️ Pulling from $BRANCH..."
git pull origin "$BRANCH"

# 6) Virtual environment
echo "🔧 Activating Python environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate

# 7) Install dependencies
echo "📦 Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# 8) Run tests
echo "🧪 Running tests..."
if ! python -m pytest --maxfail=1 --disable-warnings; then
    echo "❌ Tests failed. Rolling back..."
    rsync -av "$BACKUP_DIR/" "$BOT_DIR/" --delete
    exit 1
fi

# 9) Stop services
echo "🛑 Stopping ELBOT services..."
sudo systemctl stop elbot.service || true
sudo systemctl stop elbot-web.service || true

# 10) Deploy systemd services
echo "📄 Deploying service files..."
sudo cp elbot.service /etc/systemd/system/
sudo cp elbot-web.service /etc/systemd/system/
sudo systemctl daemon-reload

# 11) Start services
echo "🚀 Starting services..."
sudo systemctl start elbot.service
sudo systemctl start elbot-web.service

# 12) Health checks
sleep 4
if ! systemctl is-active --quiet elbot.service; then
    echo "❌ ELBOT failed to start. Rolling back..."
    rsync -av "$BACKUP_DIR/" "$BOT_DIR/" --delete
    sudo systemctl restart elbot.service
    exit 1
fi

if ! systemctl is-active --quiet elbot-web.service; then
    echo "❌ Web panel failed to start. Rolling back..."
    rsync -av "$BACKUP_DIR/" "$BOT_DIR/" --delete
    sudo systemctl restart elbot-web.service
    exit 1
fi

echo "✅ Deployment complete and services running."


