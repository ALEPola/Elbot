#!/bin/bash

# Ensure the script is run as a regular user, not root
if [ "$EUID" -eq 0 ]; then
  echo "Please do not run as root"
  exit
fi

# Navigate to the ELBOT directory
cd . || {
  echo "Directory does not exist. Exiting."
  exit 1
}

# Pull the latest changes from the Working branch
echo "Pulling the latest changes from the Git repository (Working branch)..."
git pull origin Working || {
  echo "Failed to pull the latest changes. Please check your Git configuration."
  exit 1
}

echo "Successfully updated the repository."
