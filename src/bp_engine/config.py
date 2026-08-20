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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
