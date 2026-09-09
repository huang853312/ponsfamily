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
    trace_enabled: bool
    contract_monitor_enabled: bool
    max_rpc_concurrency: int
    max_trace_concurrency: int
    max_activity_clusters: int
    activity_sample_interval: int
    external_provider_timeout: float

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
            os.getenv("TRACE_ENABLED","false").lower() in {"1","true","yes"},
            os.getenv("CONTRACT_MONITOR_ENABLED","true").lower() in {"1","true","yes"},
            int(os.getenv("MAX_RPC_CONCURRENCY","4")),int(os.getenv("MAX_TRACE_CONCURRENCY","1")),
            int(os.getenv("MAX_ACTIVITY_CLUSTERS","10")),int(os.getenv("ACTIVITY_SAMPLE_INTERVAL","60")),
            float(os.getenv("EXTERNAL_PROVIDER_TIMEOUT","10")),
        )
        if not settings.contract_monitor_enabled and not settings.v2_factories+settings.v3_factories+settings.v4_managers:
            raise ValueError("enable contract monitor or configure at least one pool factory/manager")
        if not 1<=settings.max_rpc_concurrency<=16:raise ValueError("MAX_RPC_CONCURRENCY must be 1..16")
        if not 1<=settings.max_trace_concurrency<=2:raise ValueError("MAX_TRACE_CONCURRENCY must be 1..2")
        if not 1<=settings.max_activity_clusters<=50:raise ValueError("MAX_ACTIVITY_CLUSTERS must be 1..50")
        monitor_workers=int(settings.contract_monitor_enabled)+int(bool(settings.v2_factories+settings.v3_factories+settings.v4_managers))
        if settings.max_rpc_concurrency<monitor_workers:raise ValueError("MAX_RPC_CONCURRENCY is below enabled monitor count")
        return settings
