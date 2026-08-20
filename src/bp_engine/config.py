from __future__ import annotations

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
    database_url: str = "postgresql://bp:bp_dev_only@localhost:5432/bp"
    max_trade_size_usd: float = 0
    max_daily_loss_usd: float = 0

    recorder_queue_maxsize: int = 50_000
    recorder_batch_size: int = 500
    recorder_flush_interval_seconds: float = 0.25
    polymarket_refresh_interval_seconds: float = 30.0
    polymarket_subscription_grace_seconds: float = 30.0
    recorder_stale_after_seconds: float = 10.0
    recorder_max_clock_skew_seconds: float = 5.0
    recorder_require_ntp_sync: bool = True
    polymarket_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    bybit_spot_ws_url: str = "wss://stream.bybit.com/v5/public/spot"
    bybit_linear_ws_url: str = "wss://stream.bybit.com/v5/public/linear"
    coinbase_spot_ws_url: str = "wss://advanced-trade-ws.coinbase.com"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
