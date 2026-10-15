# X Follow Monitor

Telegram bot that checks accounts followed by each monitored X account through xAPI.to and alerts only on newly observed follows.

## Runtime configuration

The service reads only \`/home/ubuntu/ponsfamily/x-follow-monitor/.env\`:

- \`TELEGRAM_BOT_TOKEN\`
- \`TELEGRAM_CHAT_ID\`
- \`XAPI_KEY\`
- \`X_FOLLOW_INTERVAL=1800\` (optional; minimum 300 seconds)
- \`X_FOLLOW_CONCURRENCY=3\` (optional)
- \`X_FOLLOW_ERROR_COOLDOWN=21600\` (optional; repeated same-category errors are limited to one message per 6 hours)
- \`X_FOLLOW_MAX_PAGES=100\` (optional; maximum cursor-paginated following pages accepted per account scan)

## Duplicate and false-alert protections

- \`/start_monitor\` preserves each account's existing follow baseline.
- Following data is cursor-paginated through \`data.cursor_bottom\`; malformed, repeated-cursor, or page-cap responses are rejected and do not overwrite a saved baseline.
- New-follow notification markers are persisted before sending, preventing the same observed relationship from being pushed again after a process interruption.
- A relationship marker is removed only after a later successful complete scan confirms that the relationship is no longer present; a later re-follow can alert again.
- Legacy state is migrated once: each monitored account's first complete paginated snapshot refreshes the baseline without alerts, preventing old single-page baselines from causing upgrade notification floods.
- Missing `data` responses across monitored accounts are grouped into one alert; startup migration merges already queued per-account messages of the same class.
- Response-shape failures log only top-level response keys and a redacted upstream error summary in `XAPI_RESPONSE_DIAGNOSTIC` records.
- All follow alerts and throttled error notices enter a durable outbound queue. The sender uses a conservative 3-second interval, honors Telegram 429 retry_after, and retries queued messages instead of treating rate-limited messages as delivered.
- Query, scan-task and main-loop error notices have persistent cooldowns; the main loop uses exponential retry backoff.
- A failed or ambiguous Telegram send is logged and not blindly retried, because Telegram's sendMessage API does not provide an idempotency key. This favors avoiding duplicate pushes over potentially repeating a message whose delivery status is uncertain.

## Safe deployment

The GitHub Actions workflow:

1. compiles and tests the release before upload;
2. validates the staged copy again on the server;
3. preserves \`.env\`, \`state.json\`, \`node_modules\`, and timestamped backups;
4. activates the release only after validation succeeds;
5. verifies that systemd remains active after startup.

The service is isolated from the HyperEVM and Robinhood Radar configuration files.
