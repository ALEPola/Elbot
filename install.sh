#!/bin/bash

# Navigate to the ELBOT directory (optional if already in it)
cd "$(dirname "$0")"

echo "🔧 Checking virtual environment..."
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
else
    echo "✅ Virtual environment already exists."
fi

echo "📲 Activating virtual environment..."
source venv/bin/activate

echo "📦 Installing requirements..."
pip install -r requirements.txt

echo "🛠️ Making deploy.sh executable..."
chmod +x deploy.sh

echo "✅ Setup complete. You can now run ./deploy.sh to update + restart the bot."
