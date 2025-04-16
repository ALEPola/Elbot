#!/bin/bash

# Navigate to the bot's folder
cd /home/alex/ELBOT

# Check for unstaged changes and stash them automatically if any exist
if [ -n "$(git status --porcelain)" ]; then
    echo "🔄 Unstaged changes detected. Stashing changes..."
    git stash push -m "Auto stash before deploy"
fi

# Pull the latest changes from the Working branch
echo "🔄 Pulling latest code from Working branch..."
git pull origin Working

# Restart the bot service
echo "🔁 Restarting ELBOT service..."
sudo systemctl restart elbot.service

echo "✅ ELBOT has been updated and restarted."
