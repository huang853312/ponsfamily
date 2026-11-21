import json
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def _json_env(name: str, default):
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must contain valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    return value


@dataclass(frozen=True)
class Settings:
    rpc_urls: dict
    chain_settings: dict
    event_filters: dict
    poll_interval: float
    default_confirmations: int
    max_block_range: int
    telegram_bot_token: str
    telegram_chat_id: str
    log_level: str


def load_settings() -> Settings:
    interval = float(os.getenv("POLL_INTERVAL", "3"))
    confirmations = int(os.getenv("DEFAULT_CONFIRMATIONS", "2"))
    block_range = int(os.getenv("MAX_BLOCK_RANGE", "100"))
    if interval <= 0 or confirmations < 0 or block_range < 1:
        raise ValueError("POLL_INTERVAL and MAX_BLOCK_RANGE must be positive; confirmations cannot be negative")

    return Settings(
        rpc_urls=_json_env("RPC_URLS", {}),
        chain_settings=_json_env("CHAIN_SETTINGS", {}),
        event_filters=_json_env("EVENT_FILTERS", {}),
        poll_interval=interval,
        default_confirmations=confirmations,
        max_block_range=block_range,
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )
