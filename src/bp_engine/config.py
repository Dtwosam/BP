from __future__ import annotations

import os
from enum import StrEnum
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class TradingMode(StrEnum):
    RESEARCH = "research"
    PAPER = "paper"
    LIVE = "live"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    mode: TradingMode = TradingMode.RESEARCH
    live_trading_enabled: bool = False
    active_horizons: tuple[str, ...] = ("5m", "15m")
    optional_horizons: tuple[str, ...] = ("10m",)
    timezone: str = "UTC"
    database_url: str = "postgresql+psycopg://bp:bp_dev_only@localhost:5432/bp"
    max_trade_size_usd: float = 0
    max_daily_loss_usd: float = 0
    max_total_exposure_usd: float = 0
    max_consecutive_losses: int = 0
    live_min_edge: float = 0
    live_min_probability: float = 0
    live_min_liquidity_usd: float = 0
    live_max_spread: float = 0
    live_max_prediction_age_seconds: float = 0
    live_min_time_to_expiry_seconds: float = 0
    live_cooldown_seconds: float = 0
    live_activation_manifest_path: str = "/var/lib/bp/live/activation.json"
    live_kill_switch_path: str = "/var/lib/bp/live/KILL"
    polymarket_geoblock_url: str = "https://polymarket.com/api/geoblock"
    polymarket_private_key_env: str = "POLYMARKET_PRIVATE_KEY"
    polymarket_wallet_address_env: str = "POLYMARKET_WALLET_ADDRESS"

    recorder_queue_maxsize: int = 50_000
    recorder_batch_size: int = 500
    recorder_flush_interval_seconds: float = 0.25
    polymarket_refresh_interval_seconds: float = 30.0
    polymarket_subscription_grace_seconds: float = 30.0
    recorder_stale_after_seconds: float = 10.0
    recorder_max_clock_skew_seconds: float = 5.0
    recorder_require_ntp_sync: bool = True

    storage_hot_raw_hours: int = 24
    storage_archive_retention_hours: int = 24
    storage_state_retention_days: int = 90
    storage_archive_dir: str = "/var/lib/bp/archive/raw"
    storage_warning_free_gib: int = 25
    storage_critical_free_gib: int = 15
    storage_delete_batch_size: int = 50_000

    polymarket_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    bybit_spot_ws_url: str = "wss://stream.bybit.com/v5/public/spot"
    bybit_linear_ws_url: str = "wss://stream.bybit.com/v5/public/linear"
    coinbase_spot_ws_url: str = "wss://advanced-trade-ws.coinbase.com"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    env_file = os.environ.get("BP_ENV_FILE")
    if env_file:
        return Settings(_env_file=env_file)
    return Settings()
