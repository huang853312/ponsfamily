# Multi-Chain Contract Event Monitor

Independent Python EVM contract-event monitor. It only reads JSON-RPC data and sends Telegram notifications. It contains no wallet, signing, swap, transaction-submission, or trading code.

## Defaults and safety
- Poll cadence: 3 seconds.
- One RPC endpoint per configured EVM chain.
- Event conditions are deliberately unset by default.
- A chain without configured addresses/topics is idle; the program does not issue unfiltered full-chain `eth_getLogs` requests.
- On first start, a chain without an explicit `start_block` initializes at its current confirmed head and does not backfill history.
- Confirmations default to 2 and can be overridden per chain.
- SQLite persists scan cursors and deduplicates events by chain + transaction hash + log index.
- Telegram failures leave events pending for retry.
- The scanner uses bounded block ranges. The 3-second interval is a polling target, not a guarantee that every chain produces a new block every 3 seconds.

## Configure
1. Copy `.env.example` to `.env`.
2. Set `RPC_URLS` to a JSON object, for example:
   `{"hyperevm":"https://YOUR_RPC","base":"https://YOUR_RPC"}`
3. Set Telegram credentials.
4. After deciding conditions, set `EVENT_FILTERS`. Example:
   `{"base":{"addresses":["0xContractAddress"],"topics":["0xEventSignatureTopic"]}}`
   Addresses and topics are optional filter dimensions, but at least one must be present for a chain to scan.
5. Optional `CHAIN_SETTINGS` supports per-chain `confirmations` and `start_block`.

Never commit `.env` or real API keys.

## VPS deployment (Ubuntu)
```bash
sudo mkdir -p /opt/multi-chain-event-monitor
sudo chown -R ubuntu:ubuntu /opt/multi-chain-event-monitor
cd /opt/multi-chain-event-monitor
git clone -b feature/multi-chain-event-monitor git@github.com:huang853312/ponsfamily.git . 
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
mkdir -p data logs
chmod 600 .env
```

Edit `.env`, then install and start the service:
```bash
sudo cp systemd/multi-chain-event-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now multi-chain-event-monitor
sudo systemctl status multi-chain-event-monitor
sudo journalctl -u multi-chain-event-monitor -f
```

The repository is a subdirectory of the existing ponsfamily repository. For a VPS that already has a separate clone of ponsfamily, fetch the branch and check out the feature branch only in a separate working directory; do not switch the existing production checkout.
