#!/usr/bin/env bash
set -euo pipefail
APP=/opt/multi-chain-auto-trader
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
sudo install -d -o trader -g trader "$APP" /var/lib/multi-chain-auto-trader /var/log/multi-chain-auto-trader
sudo rsync -a --delete --exclude='.env' "$REPO_DIR/" "$APP/"
sudo chown -R trader:trader "$APP" /var/lib/multi-chain-auto-trader /var/log/multi-chain-auto-trader
sudo -u trader python3 -m venv "$APP/.venv"
sudo -u trader "$APP/.venv/bin/pip" install --upgrade pip
sudo -u trader "$APP/.venv/bin/pip" install -r "$APP/requirements.txt"
sudo install -m 0644 "$APP/systemd/multi-chain-auto-trader.service" /etc/systemd/system/multi-chain-auto-trader.service
sudo systemctl daemon-reload
echo 'FILES_INSTALLED_ONLY: no service activation; review chain adapters before any trading.'
