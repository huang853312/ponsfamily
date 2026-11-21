#!/usr/bin/env bash
set -euo pipefail
APP=/opt/multi-chain-auto-trader
ROOT="$(cd "$(dirname "$0")" && pwd)"
if ! id trader >/dev/null 2>&1; then
  sudo useradd --system --home-dir "$APP" --create-home --shell /usr/sbin/nologin trader
fi
sudo install -d -o trader -g trader "$APP" /var/lib/multi-chain-auto-trader /var/log/multi-chain-auto-trader
sudo cp -a "$ROOT/." "$APP/"
sudo chown -R trader:trader "$APP" /var/lib/multi-chain-auto-trader /var/log/multi-chain-auto-trader
sudo -u trader python3 -m venv "$APP/.venv"
sudo -u trader "$APP/.venv/bin/pip" install --upgrade pip
sudo -u trader "$APP/.venv/bin/pip" install -r "$APP/requirements.txt"
if ! sudo test -f /etc/multi-chain-auto-trader.env; then
  TOKEN="$(openssl rand -hex 32)"
  printf '%s\n' \
    'APP_ENV=production' \
    'DATABASE_PATH=/var/lib/multi-chain-auto-trader/trader.sqlite3' \
    'RULES_FILE=/opt/multi-chain-auto-trader/config/rules.yaml' \
    'CHAINS_FILE=/opt/multi-chain-auto-trader/config/chains.yaml' \
    'SIGNAL_API_HOST=127.0.0.1' \
    'SIGNAL_API_PORT=8787' \
    "SIGNAL_API_TOKEN=$TOKEN" \
    'TRADING_ENABLED=false' \
    'SIGNER_PRIVATE_KEY=' \
    'GLOBAL_DAILY_LIMIT_USD=500' \
    'GLOBAL_SINGLE_ORDER_LIMIT_USD=100' \
    'MAX_SLIPPAGE_BPS=300' \
    'MIN_POOL_LIQUIDITY_USD=1000' \
    'MAX_GAS_COST_USD=5' \
    'MAX_PRICE_IMPACT_BPS=300' | sudo tee /etc/multi-chain-auto-trader.env >/dev/null
  sudo chown root:trader /etc/multi-chain-auto-trader.env
  sudo chmod 0640 /etc/multi-chain-auto-trader.env
fi
if ! sudo test -f "$APP/config/chains.yaml"; then
  sudo cp "$APP/config/chains.example.yaml" "$APP/config/chains.yaml"
  sudo chown trader:trader "$APP/config/chains.yaml"
fi
sudo install -m 0644 "$APP/systemd/multi-chain-auto-trader.service" /etc/systemd/system/multi-chain-auto-trader.service
sudo systemctl daemon-reload
# Default kill switch remains engaged. The service is NOT enabled or started.
sudo touch /var/lib/multi-chain-auto-trader/KILL_SWITCH
sudo chown trader:trader /var/lib/multi-chain-auto-trader/KILL_SWITCH
sudo -u trader "$APP/.venv/bin/python" -m compileall -q "$APP/app"
echo "DEPLOY_OK app=$APP unit=multi-chain-auto-trader.service trading_enabled=false service=not-started kill_switch=engaged"
