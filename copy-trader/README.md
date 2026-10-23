# Copy Trader (Robinhood Chain)

Watches wallets and replays their Pons-router swaps from your own hot wallet. Controlled entirely from Telegram. Starts **OFF**.

## How it copies
- Polls `Transfer` logs to/from the watched wallets every ~1.5 s.
- A tx from a watched wallet to the Pons router (`0x65050a9b…40dc`, `swap(...)` selector `0x4d819a2a`) is copied by reusing the exact route and replacing size, min-out (scaled from theirs minus your slippage) and deadline.
- Buy: fixed ETH amount per trade (global or per address), capped by single-trade and daily limits, keeping a gas reserve.
- Sell: sells the same fraction of your holding that the wallet sold (≥95% → sell all). Exact-amount approval just before the sell.
- Trades older than 30 s (e.g. after a restart) and non-router trades are alert-only.

## Setup (GitHub repo → Settings → Secrets → Actions)
- `COPY_TG_BOT_TOKEN` (required): a **new** bot from @BotFather. It cannot share the X monitor's bot, because both would poll the same updates.
- `COPY_PRIVATE_KEY` (optional until you want live trading): the private key of a **dedicated hot wallet** holding only what you're willing to risk.
- `COPY_RPC_URL` (optional): a private Robinhood Chain RPC (e.g. Alchemy) for faster, unthrottled polling.

`TELEGRAM_CHAT_ID` is reused from the X monitor's server `.env`. Then run the "Deploy Copy Trader" workflow, and send `/start` to the new bot in that chat.

## Commands
`/status` `/on` `/off` `/add addr label` `/del addr` `/pause addr` `/resume addr` `/list`
`/amount 0.01` `/amount addr 0.02` `/max 0.05` `/daily 0.2` `/slip 15` `/sells on|off` `/dryrun on|off` `/reserve 0.002`

## Risks
Copying a bot is always slower than the bot. You buy after them at a higher price, and if they dump first you sell lower. Keep sizes small.
