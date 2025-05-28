#!/bin/bash
set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print status messages
status() {
    echo -e "${GREEN}🔄 $1${NC}"
}

error() {
    echo -e "${RED}❌ $1${NC}"
    exit 1
}

warn() {
    echo -e "${YELLOW}⚠️ $1${NC}"
}

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    error "Please do not run this script as root/sudo"
fi

# Navigate to the ELBOT directory
cd "$(dirname "$0")"

# Check system dependencies
status "Checking system dependencies..."
for cmd in python3 pip3 git systemctl; do
    if ! command -v $cmd &> /dev/null; then
        error "$cmd is required but not installed."
    fi
done

# Check Python version
python_version=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
if (( $(echo "$python_version < 3.8" | bc -l) )); then
    error "Python 3.8 or higher is required (found $python_version)"
fi

# Create required directories
status "Setting up directories..."
sudo mkdir -p /var/log/elbot
sudo chown $USER:$USER /var/log/elbot

# Set up virtual environment
status "Setting up virtual environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
else
    warn "Virtual environment already exists, rebuilding..."
    rm -rf venv
    python3 -m venv venv
fi

# Activate virtual environment and install requirements
status "Installing dependencies..."
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Make scripts executable
status "Setting up scripts..."
chmod +x deploy.sh

# Copy and enable service files
status "Installing system services..."
sudo cp elbot.service elbot-web.service /etc/systemd/system/
sudo cp elbot-update.service elbot-update.timer /etc/systemd/system/

# Create example .env if it doesn't exist
if [ ! -f ".env" ]; then
    status "Creating example .env file..."
    cp .env.example .env || echo "No .env.example found, skipping..."
    warn "Please edit .env with your configuration"
fi

# Reload systemd and enable services
status "Enabling services..."
sudo systemctl daemon-reload
sudo systemctl enable elbot.service elbot-web.service elbot-update.timer

status "Installation complete! Next steps:"
echo "1. Edit .env with your Discord token and other settings"
echo "2. Run './deploy.sh' to start the bot"
echo "3. Check logs with 'journalctl -u elbot.service -f'"
