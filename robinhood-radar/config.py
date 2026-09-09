"""Environment-only runtime configuration."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv


def _addresses(name: str) -> tuple[str, ...]:
    return tuple(x.strip().lower() for x in os.getenv(name, "").split(",") if x.strip())


@dataclass(frozen=True)
class Settings:
    rpc_url: str
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    database_path: Path
    poll_interval: float
    block_batch_size: int
    confirmations: int
    observation_minutes: int
    v2_factories: tuple[str, ...]
    v3_factories: tuple[str, ...]
    v4_managers: tuple[str, ...]
    log_level: str

    @classmethod
    def load(cls) -> "Settings":
        load_dotenv()
        rpc = os.getenv("RPC_URL", "").strip()
        if not rpc:
            raise ValueError("RPC_URL is required")
        settings = cls(
            rpc, os.getenv("TELEGRAM_BOT_TOKEN") or None,
            os.getenv("TELEGRAM_CHAT_ID") or None,
            Path(os.getenv("DATABASE_PATH", "./data/radar.db")),
            float(os.getenv("POLL_INTERVAL_SECONDS", "3")),
            int(os.getenv("BLOCK_BATCH_SIZE", "250")),
            int(os.getenv("CONFIRMATIONS", "1")),
            int(os.getenv("OBSERVATION_MINUTES", "30")),
            _addresses("UNISWAP_V2_FACTORIES"), _addresses("UNISWAP_V3_FACTORIES"),
            _addresses("UNISWAP_V4_POOL_MANAGERS"), os.getenv("LOG_LEVEL", "INFO"),
        )
        if not settings.v2_factories + settings.v3_factories + settings.v4_managers:
            raise ValueError("configure at least one Uniswap factory/pool manager")
        return settings

