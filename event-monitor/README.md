# Independent Event Monitor

A polling-based Python monitor that reads a JSON API, applies a configurable numeric threshold, deduplicates events in SQLite, and sends Telegram notifications. It does **not** sign transactions or perform trades.

## Current adapter contract

Set `SOURCE_URL` to an HTTP endpoint returning either:

- a JSON array of event objects; or
- an object containing an `events` array.

Every event must have a stable `id`. The configured `EVENT_VALUE_FIELD` is converted to a number and compared with `EVENT_VALUE_MINIMUM`.

This is a generic API starter. A website-specific parser or an EVM log/RPC adapter must be implemented after the exact source, contract address, event signature, and chain are known.

## Local setup

```bash
cd event-monitor
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with real endpoint and Telegram credentials
python main.py
```

## VPS deployment outline

1. Clone the repository and copy this directory to `/opt/event-monitor`.
2. Create a virtual environment and install `requirements.txt`.
3. Store real configuration in `/etc/event-monitor.env` (chmod 600); do not commit secrets.
4. Review `event-monitor.service.example`, install it as `/etc/systemd/system/event-monitor.service`, then enable/start it.

## Reliability notes

- SQLite persists event IDs and pending notifications across restarts.
- An event is marked sent only after Telegram returns success; failed sends remain pending for retry.
- Logs go to console and a rotating file.
- This starter assumes one running process. Do not run multiple replicas against the same polling source/database without adding locking and cursor coordination.
- Delivery is at-least-once around the network boundary: if Telegram accepts a message but the client loses the response, a retry may duplicate it.
- API pagination, rate limits, chain reorg handling, WebSocket reconnect/backfill, and source-specific event parsing are intentionally not guessed; implement them for the selected target.
