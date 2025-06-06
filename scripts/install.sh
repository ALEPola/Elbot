#!/usr/bin/env bash

set -e

## Automated setup script for the Elbot project.
## This will create a Python virtual environment and install all required
## packages.  It also attempts to install system dependencies when run on
## Debian/Ubuntu based systems.

echo "[1/4] Checking system packages..."

# Install system packages if apt-get is available (Ubuntu/Debian)
if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y python3 python3-venv python3-pip ffmpeg
fi

echo "[2/4] Creating virtual environment..."

if [ ! -d .venv ]; then
    python3 -m venv .venv
fi

source .venv/bin/activate

echo "[3/4] Installing Python dependencies..."

# Upgrade pip first
pip install --upgrade pip

# Core dependencies used by the bot
pip install nextcord PyNaCl openai textblob python-dotenv psutil yt-dlp

# TextBlob requires NLTK corpora for sentiment analysis
python -m textblob.download_corpora

echo "[4/4] Installation complete."
echo "Activate the virtual environment with: source .venv/bin/activate"
