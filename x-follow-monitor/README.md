# X Follow Monitor

Telegram bot that checks the 20 most recent accounts followed by each monitored X account and alerts only on newly observed follows.

## Runtime configuration

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `TWITTERAPI_IO_KEY`
- `X_FOLLOW_INTERVAL=3600` (default)

The deployment workflow preserves `state.json`, installs the systemd unit, and keeps secrets outside the repository.

At a one-hour interval and a 20-profile page, the estimated API cost is about `$0.43` per monitored account per 30-day month.
