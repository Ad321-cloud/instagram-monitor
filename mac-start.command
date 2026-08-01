#!/bin/bash
set -e

cd "$(dirname "$0")"
mkdir -p logs

if [ ! -x "venv/bin/python" ]; then
  echo "Run mac-setup.command first."
  read -r -p "Press Enter to close..."
  exit 1
fi

if [ -f .monitor.pid ] && kill -0 "$(cat .monitor.pid)" 2>/dev/null; then
  echo "The monitor is already running."
  read -r -p "Press Enter to close..."
  exit 0
fi

nohup ./venv/bin/python -m app.cli run >> logs/monitor.out 2>&1 &
echo $! > .monitor.pid
echo "Monitor started. Check Telegram for the startup message."
echo "Logs: $PWD/logs/monitor.log"
read -r -p "Press Enter to close..."
