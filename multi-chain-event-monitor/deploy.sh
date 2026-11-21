#!/usr/bin/env bash
set -euo pipefail

STAGE_DIR="${1:?stage directory required}"
RELEASE_SHA="${2:-manual}"
APP_DIR="/opt/multi-chain-event-monitor"
UNIT_NAME="multi-chain-event-monitor.service"
UNIT_FILE="/etc/systemd/system/$UNIT_NAME"

test -d "$STAGE_DIR"
id ubuntu >/dev/null 2>&1 || { echo "Expected deployment user ubuntu is missing" >&2; exit 1; }

if [[ -e "$APP_DIR" && ! -f "$APP_DIR/.multi-chain-event-monitor-owned" ]]; then
  echo "Refusing to modify existing $APP_DIR: ownership marker is missing" >&2
  exit 1
fi

if [[ -e "$UNIT_FILE" ]] && ! grep -qF "WorkingDirectory=$APP_DIR" "$UNIT_FILE"; then
  echo "Refusing to replace existing $UNIT_FILE: it does not belong to this app" >&2
  exit 1
fi

install -d -o ubuntu -g ubuntu -m 0750 "$APP_DIR"
cp -a "$STAGE_DIR"/. "$APP_DIR"/
touch "$APP_DIR/.multi-chain-event-monitor-owned"
chown -R ubuntu:ubuntu "$APP_DIR"
chmod 0750 "$APP_DIR"
chmod 0640 "$APP_DIR/.env.example"

if [[ ! -f "$APP_DIR/.env" ]]; then
  install -o ubuntu -g ubuntu -m 0600 "$APP_DIR/.env.example" "$APP_DIR/.env"
else
  chown ubuntu:ubuntu "$APP_DIR/.env"
  chmod 0600 "$APP_DIR/.env"
fi

install -d -o ubuntu -g ubuntu -m 0750 "$APP_DIR/data" "$APP_DIR/logs"
sudo -u ubuntu python3 -m venv "$APP_DIR/.venv"
sudo -u ubuntu "$APP_DIR/.venv/bin/pip" install --disable-pip-version-check -r "$APP_DIR/requirements.txt"

install -o root -g root -m 0644 "$APP_DIR/systemd/multi-chain-event-monitor.service" "$UNIT_FILE"
systemctl daemon-reload
systemctl enable "$UNIT_NAME"
systemctl restart "$UNIT_NAME"

echo "DEPLOY_OK app=$APP_DIR unit=$UNIT_NAME release=$RELEASE_SHA"
systemctl --no-pager --full status "$UNIT_NAME" || true
