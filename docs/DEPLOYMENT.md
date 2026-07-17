# Deployment Guide — Oracle Cloud Always Free

## Prerequisites

- Oracle Cloud account with Always Free tier
- An Always Free VM instance (Ubuntu 22.04+)
- SSH access to the instance

## Step 1: Server Setup

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Python 3.12+
sudo apt install -y python3.12 python3.12-venv python3.12-dev python3-pip git

# Create application user (security best practice)
sudo useradd -m -s /bin/bash igmonitor
sudo su - igmonitor
```

## Step 2: Clone & Install

```bash
# Clone the repository
git clone <your-repo-url> ~/instagram-monitor
cd ~/instagram-monitor

# Create virtual environment
python3.12 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Step 3: Configure Environment

```bash
# Copy and edit environment file
cp .env.example .env
nano .env  # Fill in your Supabase and Telegram credentials
```

## Step 4: Test the Installation

```bash
# Verify database connection
python -m app.main &  # Should start and connect
# Press Ctrl+C to stop

# Test CLI
python -m app.cli list
python -m app.cli add testusername
python -m app.cli check testusername
```

## Step 5: Create systemd Service

```bash
sudo tee /etc/systemd/system/instagram-monitor.service << 'EOF'
[Unit]
Description=Instagram Username Monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=igmonitor
Group=igmonitor
WorkingDirectory=/home/igmonitor/instagram-monitor
Environment=PATH=/home/igmonitor/instagram-monitor/venv/bin:/usr/bin
ExecStart=/home/igmonitor/instagram-monitor/venv/bin/python -m app.main
Restart=always
RestartSec=10
StandardOutput=append:/var/log/instagram-monitor/service.log
StandardError=append:/var/log/instagram-monitor/service.log

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/home/igmonitor/instagram-monitor/logs

[Install]
WantedBy=multi-user.target
EOF
```

## Step 6: Create Log Directory

```bash
sudo mkdir -p /var/log/instagram-monitor
sudo chown igmonitor:igmonitor /var/log/instagram-monitor
```

## Step 7: Enable & Start

```bash
# Reload systemd
sudo systemctl daemon-reload

# Enable on boot
sudo systemctl enable instagram-monitor

# Start the service
sudo systemctl start instagram-monitor

# Check status
sudo systemctl status instagram-monitor
```

## Step 8: Verify It's Running

```bash
# Check service status
sudo systemctl status instagram-monitor

# Follow logs
sudo journalctl -u instagram-monitor -f

# Check application logs
tail -f /home/igmonitor/instagram-monitor/logs/monitor.log
```

## Useful Commands

```bash
# Stop the service
sudo systemctl stop instagram-monitor

# Restart
sudo systemctl restart instagram-monitor

# View recent logs
sudo journalctl -u instagram-monitor --since "1 hour ago"

# Add a username (while service is running)
cd /home/igmonitor/instagram-monitor
source venv/bin/activate
python -m app.cli add new_username

# Check service resource usage
systemctl show instagram-monitor --property=MemoryCurrent
```

## Oracle Cloud Firewall Note

The Instagram monitor is an outbound-only service — it makes requests TO Instagram and Telegram. No inbound firewall rules are needed. The default Oracle Cloud security list (which blocks all inbound except SSH) is perfect.

## Monitoring the Monitor

Set up a simple cron job to check if the service is running:

```bash
# Add to crontab: crontab -e
*/30 * * * * systemctl is-active --quiet instagram-monitor || systemctl restart instagram-monitor
```
