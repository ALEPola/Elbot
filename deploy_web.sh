#!/bin/bash

# Ensure the script is run as root
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root"
  exit
fi

# Activate virtual environment and install Gunicorn
source ./venv/bin/activate
pip install gunicorn

deactivate

# Copy the service file to systemd directory
cp ./elbot-web.service /etc/systemd/system/

# Reload systemd and restart the service
systemctl daemon-reload
systemctl restart elbot-web.service

# Enable the service to start on boot
systemctl enable elbot-web.service

# Check the status of the service
systemctl status elbot-web.service
