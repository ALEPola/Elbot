#!/bin/bash

# Navigate to the bot's folder
cd /home/alex/ELBOT

# Pull the latest changes from the main branch
git pull origin Testing

# Restart the bot service
sudo systemctl restart elbot.service

echo "✅ ELBOT has been updated and restarted."
