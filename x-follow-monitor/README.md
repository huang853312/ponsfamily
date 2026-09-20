# X Follow Monitor

Telegram bot that checks accounts followed by each monitored X account through xAPI.to and alerts only on newly observed follows.

## Runtime configuration

The service reads only `/home/ubuntu/ponsfamily/x-follow-monitor/.env`:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `XAPI_KEY`
- `X_FOLLOW_INTERVAL=1800` (optional; minimum 300 seconds)
- `X_FOLLOW_CONCURRENCY=3` (optional)

## Safe deployment

The GitHub Actions workflow:

1. compiles and tests the release before upload;
2. validates the staged copy again on the server;
3. preserves `.env`, `state.json`, `node_modules`, and timestamped backups;
4. activates the release only after validation succeeds;
5. verifies that systemd remains active after startup.

The service is isolated from the HyperEVM and Robinhood Radar configuration files.
