#!/bin/bash
set -e

echo "🚀 Setting up Instagram Monitor on Oracle Cloud..."

# 1. Update and install dependencies
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip git

# 2. Setup Virtual Environment
echo "📦 Setting up Python environment..."
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Setup Systemd Service
echo "⚙️ Setting up systemd service..."
# Replace 'ubuntu' with actual username in the service file
sed -i "s/User=ubuntu/User=$USER/g" instagram-monitor.service
sed -i "s|/home/ubuntu/instagram-monitor|$(pwd)|g" instagram-monitor.service

sudo cp instagram-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable instagram-monitor.service
sudo systemctl start instagram-monitor.service

echo ""
echo "✅ Deployment Complete! The bot is now running in the background."
echo "You can check the live logs at any time using:"
echo "sudo journalctl -u instagram-monitor -f"
