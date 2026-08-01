# Local Deployment

The application can run on a local Linux PC or macOS machine. It uses Supabase as
the shared PostgreSQL database and Telegram for notifications. Do not run the same
Telegram bot token on two machines simultaneously.

## One-time Setup

Install Python 3.12 or newer, then clone the repository:

```bash
git clone <your-repo-url>
cd instagram-monitor
python3.12 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env
```

Edit `.env` and fill in:

- `DATABASE_URL`: the Supabase Session pooler URI.
- `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

Never commit `.env` or share it with the other machine. Copy the values through a
secure channel and rotate the keys if they are exposed.

## Run On This PC

```bash
cd /path/to/instagram-monitor
source venv/bin/activate
python -m app.cli run
```

Keep this terminal open. Stop with `Ctrl+C`.

For automatic startup on Ubuntu/Linux, use the existing systemd service:

```bash
sudo sed -i "s|/home/ubuntu/instagram-monitor|$(pwd)|g; s/User=ubuntu/User=$USER/" instagram-monitor.service
sudo cp instagram-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now instagram-monitor
sudo journalctl -u instagram-monitor -f
```

## Run On macOS

```bash
brew install python@3.12
git clone <your-repo-url>
cd instagram-monitor
python3.12 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python -m app.cli run
```

The Mac must remain powered on, connected to the internet, and awake. To keep it
running after closing Terminal, use:

```bash
cd /path/to/instagram-monitor
source venv/bin/activate
nohup python -m app.cli run > logs/monitor.out 2>&1 &
```

Stop that process later with:

```bash
pkill -f "python -m app.cli run"
```

## Switching Machines

1. Stop the bot on the current machine.
2. Pull the latest code on the other machine:
   ```bash
   git pull origin main
   ```
3. Confirm that machine has the same `.env` values.
4. Start the bot there with `python -m app.cli run`.
5. Confirm Telegram sends one startup notification.

If this PC is closed or powered off, the bot stops monitoring. The Mac can take
over, but it must be started first and the PC process must be stopped. For seamless
failover, use one always-on machine or a small supervisor that starts the service
on only one host.
