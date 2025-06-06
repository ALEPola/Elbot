#!/usr/bin/env bash

set -e

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

## Automated setup script for the Elbot project.
## This will create a Python virtual environment and install all required
## packages.  It also attempts to install system dependencies when run on
## Debian/Ubuntu based systems.

echo "[1/5] Checking system packages..."

# Install system packages if apt-get is available (Ubuntu/Debian)
if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y python3 python3-venv python3-pip ffmpeg
fi

echo "[2/5] Creating virtual environment..."

if [ ! -d "$ROOT_DIR/.venv" ]; then
    python3 -m venv "$ROOT_DIR/.venv"
fi

source "$ROOT_DIR/.venv/bin/activate"

# Copy example environment if none exists
if [ ! -f "$ROOT_DIR/.env" ] && [ -f "$ROOT_DIR/.env.example" ]; then
    cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
fi

echo "[3/5] Installing Python dependencies..."

# Upgrade pip first
pip install --upgrade pip

# Core dependencies used by the bot
pip install nextcord PyNaCl openai textblob python-dotenv psutil yt-dlp

# TextBlob requires NLTK corpora for sentiment analysis
python -m textblob.download_corpora

echo "[4/5] Installing systemd service..."

if command -v systemctl >/dev/null 2>&1; then
    SERVICE_FILE="/etc/systemd/system/elbot.service"
    sudo tee "$SERVICE_FILE" >/dev/null <<EOF
[Unit]
Description=Elbot Discord Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=$ROOT_DIR
ExecStart=$ROOT_DIR/.venv/bin/python -m elbot.main
EnvironmentFile=$ROOT_DIR/.env
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable elbot.service
    echo "Service installed as elbot.service"
else
    echo "systemctl not found; skipping service installation"
fi

echo "[5/5] Installation complete."
echo "Activate the virtual environment with: source .venv/bin/activate"
