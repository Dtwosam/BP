from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, Select, select

from bp_engine.storage.schema import btc_candles, market_state_1s, polymarket_price_history


class FeatureLeakageError(RuntimeError):
    """Raised when a selected source row contains information from after feature time."""


def _utc(value: datetime, name: str = "timestamp") -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _stored_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclass(frozen=True)
class PriceObservation:
    row_id: int
    source: str
    condition_id: str
    asset_id: str
    outcome: str
    observed_at: datetime
    price: Decimal
    fidelity_minutes: int

    @property
    def effective_at(self) -> datetime:
        return self.observed_at


@dataclass(frozen=True)
class CandleObservation:
    row_id: int
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

    @property
    def effective_at(self) -> datetime:
        return self.bucket_at + timedelta(seconds=self.interval_seconds)


@dataclass(frozen=True)
class StateObservation:
    row_id: int
    bucket_at: datetime
    state_key: str
    source: str
    stream: str
    instrument: str
    market_id: str | None
    asset_id: str | None
    last_event_at: datetime
    state: dict[str, Any]
    fresh: bool
    age_seconds: float

    @property
    def effective_at(self) -> datetime:
        return max(self.bucket_at, self.last_event_at)


class FeatureSourceReader:
    def __init__(self, *, state_fresh_seconds: float = 10.0) -> None:
        if state_fresh_seconds <= 0:
            raise ValueError("state_fresh_seconds must be positive")
        self._state_fresh_seconds = state_fresh_seconds

    @staticmethod
    def _one(connection: Connection, statement: Select[Any]) -> dict[str, Any] | None:
        row = connection.execute(statement).mappings().first()
        return dict(row) if row is not None else None

    def latest_polymarket_price(
        self,
        connection: Connection,
        *,
        condition_id: str,
        outcome: str,
        feature_at: datetime,
    ) -> PriceObservation | None:
        cutoff = _utc(feature_at, "feature_at")
        row = self._one(
            connection,
            select(polymarket_price_history)
            .where(
                polymarket_price_history.c.condition_id == condition_id,
                polymarket_price_history.c.outcome == outcome,
                polymarket_price_history.c.observed_at <= cutoff,
            )
            .order_by(
                polymarket_price_history.c.observed_at.desc(),
                polymarket_price_history.c.id.desc(),
            )
            .limit(1),
        )
        if row is None:
            return None
        observed_at = _stored_utc(row["observed_at"])
        if observed_at > cutoff:
            raise FeatureLeakageError("polymarket price observed_at exceeds feature_at")
        return PriceObservation(
            row_id=int(row["id"]),
            source=str(row["source"]),
            condition_id=str(row["condition_id"]),
            asset_id=str(row["asset_id"]),
            outcome=str(row["outcome"]),
            observed_at=observed_at,
            price=Decimal(row["price"]),
            fidelity_minutes=int(row["fidelity_minutes"]),
        )

    def closed_candles(
        self,
        connection: Connection,
        *,
        source: str,
        market_type: str,
        symbol: str,
        interval_seconds: int,
        feature_at: datetime,
        limit: int,
    ) -> tuple[CandleObservation, ...]:
        cutoff = _utc(feature_at, "feature_at")
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        if limit <= 0:
            raise ValueError("limit must be positive")
        latest_bucket = cutoff - timedelta(seconds=interval_seconds)
        rows = connection.execute(
            select(btc_candles)
            .where(
                btc_candles.c.source == source,
                btc_candles.c.market_type == market_type,
                btc_candles.c.symbol == symbol,
                btc_candles.c.interval_seconds == interval_seconds,
                btc_candles.c.bucket_at <= latest_bucket,
            )
            .order_by(btc_candles.c.bucket_at.desc(), btc_candles.c.id.desc())
            .limit(limit)
        ).mappings().all()
        observations: list[CandleObservation] = []
        for raw in reversed(rows):
            row = dict(raw)
            bucket_at = _stored_utc(row["bucket_at"])
            observation = CandleObservation(
                row_id=int(row["id"]),
                source=str(row["source"]),
                market_type=str(row["market_type"]),
                symbol=str(row["symbol"]),
                interval_seconds=int(row["interval_seconds"]),
                bucket_at=bucket_at,
                open=Decimal(row["open"]),
                high=Decimal(row["high"]),
                low=Decimal(row["low"]),
                close=Decimal(row["close"]),
                volume=Decimal(row["volume"]),
                turnover=(Decimal(row["turnover"]) if row["turnover"] is not None else None),
            )
            if observation.effective_at > cutoff:
                raise FeatureLeakageError("candle close exceeds feature_at")
            observations.append(observation)
        return tuple(observations)

    def latest_state(
        self,
        connection: Connection,
        *,
        source: str,
        stream: str,
        instrument: str,
        feature_at: datetime,
        asset_id: str | None = None,
    ) -> StateObservation | None:
        cutoff = _utc(feature_at, "feature_at")
        statement = select(market_state_1s).where(
            market_state_1s.c.source == source,
            market_state_1s.c.stream == stream,
            market_state_1s.c.instrument == instrument,
            market_state_1s.c.bucket_at <= cutoff,
            market_state_1s.c.last_event_at <= cutoff,
        )
        if asset_id is None:
            statement = statement.where(market_state_1s.c.asset_id.is_(None))
        else:
            statement = statement.where(market_state_1s.c.asset_id == asset_id)
        row = self._one(
            connection,
            statement.order_by(
                market_state_1s.c.bucket_at.desc(),
                market_state_1s.c.id.desc(),
            ).limit(1),
        )
        if row is None:
            return None
        bucket_at = _stored_utc(row["bucket_at"])
        last_event_at = _stored_utc(row["last_event_at"])
        if bucket_at > cutoff:
            raise FeatureLeakageError("state bucket_at exceeds feature_at")
        if last_event_at > cutoff:
            raise FeatureLeakageError("state last_event_at exceeds feature_at")
        age_seconds = (cutoff - last_event_at).total_seconds()
        return StateObservation(
            row_id=int(row["id"]),
            bucket_at=bucket_at,
            state_key=str(row["state_key"]),
            source=str(row["source"]),
            stream=str(row["stream"]),
            instrument=str(row["instrument"]),
            market_id=(str(row["market_id"]) if row["market_id"] is not None else None),
            asset_id=(str(row["asset_id"]) if row["asset_id"] is not None else None),
            last_event_at=last_event_at,
            state=dict(row["state"]),
            fresh=age_seconds <= self._state_fresh_seconds,
            age_seconds=age_seconds,
        )
