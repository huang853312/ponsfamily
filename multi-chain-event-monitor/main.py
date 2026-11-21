import logging
import os
import time
from config import load_settings
from database import EventStore
from scanner import ChainScanner
from telegram import TelegramNotifier


def format_event(chain: str, item: dict) -> str:
    tx = item.get("transactionHash", "")
    block = item.get("blockNumber", "")
    address = item.get("address", "")
    topics = item.get("topics", [])
    return (
        f"合约事件提醒 | {chain}\n"
        f"区块: {block}\n"
        f"合约: {address}\n"
        f"交易: {tx}\n"
        f"Topics: {topics}"
    )


def main():
    settings = load_settings()
    os.makedirs("logs", exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler("logs/monitor.log", encoding="utf-8")],
    )
    store = EventStore()
    notifier = None
    if any(v.get("addresses") or v.get("topics") for v in settings.event_filters.values()):
        notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)

    scanners = {}
    for chain, url in settings.rpc_urls.items():
        scanners[chain] = ChainScanner(
            chain, url, store,
            settings.chain_settings.get(chain, {}),
            settings.event_filters.get(chain, {}),
            {"confirmations": settings.default_confirmations, "max_range": settings.max_block_range},
        )

    if not scanners:
        logging.warning("No RPC_URLS configured. Service will remain idle until chain endpoints are configured.")

    logging.info("multi-chain event monitor started; chains=%s interval=%ss; trading=disabled",
                 list(scanners), settings.poll_interval)

    try:
        while True:
            started = time.monotonic()
            for chain, scanner in scanners.items():
                try:
                    scanner.scan_once()
                except Exception:
                    logging.exception("[%s] scan failed; cursor retained for retry", chain)

            if notifier:
                for row in store.pending_events():
                    try:
                        import json
                        payload = json.loads(row["payload"])
                        notifier.send(format_event(row["chain"], payload))
                        store.mark_sent(row["event_key"])
                    except Exception:
                        logging.exception("Telegram delivery failed for %s; will retry", row["event_key"])

            elapsed = time.monotonic() - started
            time.sleep(max(0, settings.poll_interval - elapsed))
    finally:
        store.close()


if __name__ == "__main__":
    main()
