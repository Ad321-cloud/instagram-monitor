#!/bin/bash
set -e

cd "$(dirname "$0")"

if [ -f .monitor.pid ] && kill -0 "$(cat .monitor.pid)" 2>/dev/null; then
  kill "$(cat .monitor.pid)"
  rm -f .monitor.pid
  echo "Monitor stopped."
else
  echo "Monitor is not running."
  rm -f .monitor.pid
fi

read -r -p "Press Enter to close..."
