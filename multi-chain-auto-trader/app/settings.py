from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path
import yaml

@dataclass(frozen=True)
class Settings:
    database_path: str
    rules_file: str
    chains_file: str
    signal_api_token: str
    trading_enabled: bool
    single_order_limit_usd: float
    daily_limit_usd: float
    max_slippage_bps: int
    min_pool_liquidity_usd: float
    max_gas_cost_usd: float
    max_price_impact_bps: int

    @classmethod
    def from_env(cls) -> 'Settings':
        return cls(
            database_path=os.getenv('DATABASE_PATH', './trader.sqlite3'),
            rules_file=os.getenv('RULES_FILE', './config/rules.yaml'),
            chains_file=os.getenv('CHAINS_FILE', './config/chains.yaml'),
            signal_api_token=os.getenv('SIGNAL_API_TOKEN', ''),
            trading_enabled=os.getenv('TRADING_ENABLED', 'false').lower() == 'true',
            single_order_limit_usd=float(os.getenv('GLOBAL_SINGLE_ORDER_LIMIT_USD', '100')),
            daily_limit_usd=float(os.getenv('GLOBAL_DAILY_LIMIT_USD', '500')),
            max_slippage_bps=int(os.getenv('MAX_SLIPPAGE_BPS', '300')),
            min_pool_liquidity_usd=float(os.getenv('MIN_POOL_LIQUIDITY_USD', '1000')),
            max_gas_cost_usd=float(os.getenv('MAX_GAS_COST_USD', '5')),
            max_price_impact_bps=int(os.getenv('MAX_PRICE_IMPACT_BPS', '300')),
        )

def load_yaml(path: str) -> dict:
    p = Path(path)
    if not p.exists(): return {}
    with p.open('r', encoding='utf-8') as f: return yaml.safe_load(f) or {}

def validate_settings(s: Settings) -> None:
    if s.single_order_limit_usd <= 0 or s.daily_limit_usd <= 0: raise ValueError('Limits must be positive')
    if s.single_order_limit_usd > s.daily_limit_usd: raise ValueError('Single order limit exceeds daily limit')
    if not 1 <= s.max_slippage_bps <= 300: raise ValueError('Slippage must be between 1 and 300 bps')
    if s.min_pool_liquidity_usd < 1000: raise ValueError('Liquidity floor cannot be lower than $1,000')
    if s.max_gas_cost_usd <= 0: raise ValueError('Gas cap must be positive')
