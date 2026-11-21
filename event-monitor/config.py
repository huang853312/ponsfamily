import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_chat_id: str
    source_url: str
    source_bearer_token: str
    poll_interval_seconds: int
    request_timeout_seconds: int
    event_value_field: str
    event_value_minimum: float
    database_path: Path
    log_path: Path


def load_settings() -> Settings:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    source_url = os.getenv("SOURCE_URL", "").strip()

    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
    if not source_url:
        raise RuntimeError("SOURCE_URL is required")

    poll_interval = int(os.getenv("POLL_INTERVAL_SECONDS", "10"))
    timeout = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "15"))
    if poll_interval < 1 or timeout < 1:
        raise ValueError("Polling interval and request timeout must be positive")

    return Settings(
        telegram_bot_token=token,
        telegram_chat_id=chat_id,
        source_url=source_url,
        source_bearer_token=os.getenv("SOURCE_BEARER_TOKEN", "").strip(),
        poll_interval_seconds=poll_interval,
        request_timeout_seconds=timeout,
        event_value_field=os.getenv("EVENT_VALUE_FIELD", "value").strip(),
        event_value_minimum=float(os.getenv("EVENT_VALUE_MINIMUM", "1000")),
        database_path=Path(os.getenv("DATABASE_PATH", "data/events.sqlite3")),
        log_path=Path(os.getenv("LOG_PATH", "logs/monitor.log")),
    )
