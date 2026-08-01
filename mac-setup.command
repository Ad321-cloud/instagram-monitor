#!/bin/bash
set -e

cd "$(dirname "$0")"

echo "Instagram Monitor - Mac Setup"
echo ""

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required. Install it from https://brew.sh, then run this file again."
  read -r -p "Press Enter to close..."
  exit 1
fi

brew install python@3.12 >/dev/null 2>&1 || true

PYTHON_BIN="$(brew --prefix python@3.12)/bin/python3.12"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="$(command -v python3.12 || true)"
fi
if [ -z "$PYTHON_BIN" ] || [ ! -x "$PYTHON_BIN" ]; then
  echo "Python 3.12 could not be found."
  read -r -p "Press Enter to close..."
  exit 1
fi

"$PYTHON_BIN" -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo ""
echo "Enter the shared Supabase and Telegram settings. Values are saved only in this Mac's .env file."
read -r -p "Supabase pooler host: " DB_HOST
read -r -p "Pooler database user: " DB_USER
read -r -s -p "Database password: " DB_PASSWORD
echo ""
read -r -p "Telegram bot token: " TELEGRAM_BOT_TOKEN
read -r -p "Telegram chat ID: " TELEGRAM_CHAT_ID

cat > .env <<EOF
DB_HOST=$DB_HOST
DB_PORT=5432
DB_NAME=postgres
DB_USER=$DB_USER
DB_PASSWORD=$DB_PASSWORD
TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID=$TELEGRAM_CHAT_ID
CHECK_INTERVAL_SECONDS=120
CHECK_DELAY_SECONDS=10
MAX_CONCURRENT_CHECKS=3
LOG_LEVEL=INFO
LOG_DIR=logs
ENVIRONMENT=development
EOF

chmod +x mac-start.command mac-stop.command
mkdir -p logs

echo ""
echo "Setup complete. Double-click mac-start.command to start monitoring."
read -r -p "Press Enter to close..."
