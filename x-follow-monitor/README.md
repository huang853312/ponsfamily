# X Follow Monitor

Telegram bot that checks accounts followed by each monitored X account through xAPI.to and alerts only on newly observed follows.

## Runtime configuration

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `XAPI_KEY`
- `X_FOLLOW_INTERVAL=1800` (default)

The deployment workflow preserves `state.json`, installs the systemd unit, and keeps secrets outside the repository. The default scan interval is 30 minutes.
