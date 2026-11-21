import json
import logging
import time
from logging.handlers import RotatingFileHandler

from config import load_settings
from monitor import EventSource, matches_condition
from notifier import TelegramNotifier
from storage import EventStore


def configure_logging(log_path):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    file_handler = RotatingFileHandler(
        log_path, maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root.addHandler(file_handler)
    root.addHandler(console_handler)


def main():
    settings = load_settings()
    configure_logging(settings.log_path)

    source = EventSource(
        settings.source_url,
        settings.source_bearer_token,
        settings.request_timeout_seconds,
    )
    notifier = TelegramNotifier(
        settings.telegram_bot_token,
        settings.telegram_chat_id,
        settings.request_timeout_seconds,
    )
    store = EventStore(settings.database_path)

    logging.info("Event monitor started; interval=%ss", settings.poll_interval_seconds)

    while True:
        try:
            events = source.fetch_events()
            for event in events:
                if matches_condition(
                    event, settings.event_value_field, settings.event_value_minimum
                ):
                    event_id = str(event["id"])
                    store.enqueue(
                        event_id,
                        json.dumps(event, ensure_ascii=False, separators=(",", ":")),
                    )

            # Retry pending notifications after transient Telegram/network failures.
            for event_id, payload in store.pending():
                try:
                    event = json.loads(payload)
                    message = (
                        "监控事件触发\n"
                        f"事件 ID: {event_id}\n"
                        f"事件数据: {json.dumps(event, ensure_ascii=False)}"
                    )
                    store.mark_attempt(event_id)
                    notifier.send(message)
                    store.mark_sent(event_id)
                    logging.info("Notification sent: event_id=%s", event_id)
                except Exception:
                    logging.exception("Notification failed; will retry: event_id=%s", event_id)

        except Exception:
            logging.exception("Polling cycle failed")

        time.sleep(settings.poll_interval_seconds)


if __name__ == "__main__":
    main()
