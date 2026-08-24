from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import Connection, insert, select

from bp_engine.storage.schema import (
    btc_candles,
    polymarket_market_snapshots,
    polymarket_price_history,
)


class HistoricalDataConflict(RuntimeError):
    """Raised when a provider attempts to rewrite an existing historical observation."""


@dataclass(frozen=True)
class StoreResult:
    created: bool


@dataclass(frozen=True)
class PolymarketMarketSnapshot:
    condition_id: str
    gamma_market_id: str
    slug: str
    downloaded_at: datetime
    payload_sha256: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class PolymarketPricePoint:
    condition_id: str
    asset_id: str
    outcome: Literal["Up", "Down"]
    observed_at: datetime
    price: Decimal
    fidelity_minutes: int
    source: str = "polymarket_clob_prices_history"


@dataclass(frozen=True)
class BtcCandle:
    source: str
    market_type: str
    symbol: str
    interval_seconds: int
    bucket_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    turnover: Decimal | None
    raw_payload: Any


class HistoricalRepository:
    def store_polymarket_market_snapshot(
        self,
        connection: Connection,
        snapshot: PolymarketMarketSnapshot,
    ) -> StoreResult:
        self._require_aware(snapshot.downloaded_at, "downloaded_at")
        existing = connection.execute(
            select(polymarket_market_snapshots).where(
                polymarket_market_snapshots.c.condition_id == snapshot.condition_id,
                polymarket_market_snapshots.c.payload_sha256 == snapshot.payload_sha256,
            )
        ).mappings().one_or_none()
        if existing is not None:
            return StoreResult(created=False)

        connection.execute(
            insert(polymarket_market_snapshots).values(
                condition_id=snapshot.condition_id,
                gamma_market_id=snapshot.gamma_market_id,
                slug=snapshot.slug,
                downloaded_at=snapshot.downloaded_at,
                payload_sha256=snapshot.payload_sha256,
                payload=snapshot.payload,
            )
        )
        return StoreResult(created=True)

    def store_polymarket_price(
        self,
        connection: Connection,
        point: PolymarketPricePoint,
    ) -> StoreResult:
        self._require_aware(point.observed_at, "observed_at")
        if point.fidelity_minutes <= 0:
            raise ValueError("fidelity_minutes must be positive")

        existing = connection.execute(
            select(polymarket_price_history).where(
                polymarket_price_history.c.asset_id == point.asset_id,
                polymarket_price_history.c.observed_at == point.observed_at,
                polymarket_price_history.c.fidelity_minutes == point.fidelity_minutes,
            )
        ).mappings().one_or_none()

        if existing is None:
            connection.execute(
                insert(polymarket_price_history).values(
                    source=point.source,
                    condition_id=point.condition_id,
                    asset_id=point.asset_id,
                    outcome=point.outcome,
                    observed_at=point.observed_at,
                    price=point.price,
                    fidelity_minutes=point.fidelity_minutes,
                )
            )
            return StoreResult(created=True)

        expected = (
            point.source,
            point.condition_id,
            point.outcome,
            point.price,
        )
        actual = (
            existing["source"],
            existing["condition_id"],
            existing["outcome"],
            existing["price"],
        )
        if actual != expected:
            raise HistoricalDataConflict(
                "conflicting Polymarket price history for "
                f"asset={point.asset_id} observed_at={point.observed_at.isoformat()} "
                f"fidelity={point.fidelity_minutes}"
            )
        return StoreResult(created=False)

    def store_btc_candle(
        self,
        connection: Connection,
        candle: BtcCandle,
    ) -> StoreResult:
        self._require_aware(candle.bucket_at, "bucket_at")
        if candle.interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")

        existing = connection.execute(
            select(btc_candles).where(
                btc_candles.c.source == candle.source,
                btc_candles.c.market_type == candle.market_type,
                btc_candles.c.symbol == candle.symbol,
                btc_candles.c.interval_seconds == candle.interval_seconds,
                btc_candles.c.bucket_at == candle.bucket_at,
            )
        ).mappings().one_or_none()

        if existing is None:
            connection.execute(
                insert(btc_candles).values(
                    source=candle.source,
                    market_type=candle.market_type,
                    symbol=candle.symbol,
                    interval_seconds=candle.interval_seconds,
                    bucket_at=candle.bucket_at,
                    open=candle.open,
                    high=candle.high,
                    low=candle.low,
                    close=candle.close,
                    volume=candle.volume,
                    turnover=candle.turnover,
                    raw_payload=candle.raw_payload,
                )
            )
            return StoreResult(created=True)

        expected = (
            candle.open,
            candle.high,
            candle.low,
            candle.close,
            candle.volume,
            candle.turnover,
        )
        actual = (
            existing["open"],
            existing["high"],
            existing["low"],
            existing["close"],
            existing["volume"],
            existing["turnover"],
        )
        if actual != expected:
            raise HistoricalDataConflict(
                "conflicting BTC candle for "
                f"source={candle.source} market_type={candle.market_type} "
                f"symbol={candle.symbol} interval={candle.interval_seconds} "
                f"bucket_at={candle.bucket_at.isoformat()}"
            )
        return StoreResult(created=False)

    @staticmethod
    def _require_aware(value: datetime, name: str) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{name} must be timezone-aware")
