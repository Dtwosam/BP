from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import Connection, select

from bp_engine.features.exclusions import raw_window_exclusion
from bp_engine.storage.schema import raw_market_events


class TradeFlowError(RuntimeError):
    """Raised when a trade event cannot be interpreted without guessing."""


@dataclass(frozen=True)
class TradeFlow:
    buy_volume: Decimal
    sell_volume: Decimal
    signed_volume: Decimal
    trade_count: int
    observations: tuple[dict[str, object], ...]
    coverage_cutoff: datetime
    coverage_observation: dict[str, object]


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _stored_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _decimal(value: object, name: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise TradeFlowError(f"{name} must be numeric") from exc
    if not result.is_finite() or result < 0:
        raise TradeFlowError(f"{name} must be finite and non-negative")
    return result


def _side(value: object) -> str:
    normalized = str(value).strip().upper() if value is not None else ""
    if normalized == "BUY":
        return "BUY"
    if normalized == "SELL":
        return "SELL"
    raise TradeFlowError("trade side must be explicitly BUY or SELL")


def _payload(event: Mapping[str, object]) -> Mapping[str, object]:
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        raise TradeFlowError("trade event payload must be a mapping")
    return payload


def _polymarket_trades(event: Mapping[str, object]) -> list[Mapping[str, object]]:
    if str(event.get("event_type")) != "last_trade_price":
        return []
    payload = _payload(event)
    if payload.get("size") is None or payload.get("side") is None:
        raise TradeFlowError("Polymarket trade requires explicit side and size")
    return [payload]


def _coinbase_trades(event: Mapping[str, object]) -> list[Mapping[str, object]]:
    if not str(event.get("event_type", "")).startswith("market_trades_"):
        return []
    payload = _payload(event)
    groups = payload.get("events")
    if not isinstance(groups, list):
        raise TradeFlowError("Coinbase market_trades event requires events list")
    result: list[Mapping[str, object]] = []
    for group in groups:
        if not isinstance(group, Mapping):
            raise TradeFlowError("Coinbase trade group must be a mapping")
        trades = group.get("trades")
        if not isinstance(trades, list):
            raise TradeFlowError("Coinbase trade group requires trades list")
        for trade in trades:
            if not isinstance(trade, Mapping):
                raise TradeFlowError("Coinbase trade must be a mapping")
            result.append(trade)
    return result


def _bybit_trades(event: Mapping[str, object]) -> list[Mapping[str, object]]:
    if str(event.get("event_type")) != "trade":
        return []
    payload = _payload(event)
    trades = payload.get("data")
    if not isinstance(trades, list):
        raise TradeFlowError("Bybit trade event requires data list")
    result: list[Mapping[str, object]] = []
    for trade in trades:
        if not isinstance(trade, Mapping):
            raise TradeFlowError("Bybit trade must be a mapping")
        result.append(trade)
    return result


def _trades(
    event: Mapping[str, object], *, source: str
) -> list[tuple[str, Decimal, object | None]]:
    if source == "polymarket":
        raw_trades = _polymarket_trades(event)
        side_key, size_key, price_key = "side", "size", "price"
    elif source == "coinbase":
        raw_trades = _coinbase_trades(event)
        side_key, size_key, price_key = "side", "size", "price"
    elif source == "bybit":
        raw_trades = _bybit_trades(event)
        side_key, size_key, price_key = "S", "v", "p"
    else:
        raise ValueError(f"unsupported trade-flow source: {source}")

    result: list[tuple[str, Decimal, object | None]] = []
    for trade in raw_trades:
        if trade.get(size_key) is None:
            raise TradeFlowError(f"{source} trade requires size")
        result.append(
            (
                _side(trade.get(side_key)),
                _decimal(trade[size_key], "trade size"),
                trade.get(price_key),
            )
        )
    return result


def _coverage_descriptor(
    event: Mapping[str, object], *, source: str, stream: str, received_at: datetime
) -> dict[str, object]:
    return {
        "kind": "raw_feed_coverage",
        "dedupe_key": str(event.get("dedupe_key", "")),
        "source": source,
        "stream": stream,
        "event_type": str(event.get("event_type", "")),
        "received_at": received_at,
    }


def parse_trade_flow(
    events: Iterable[Mapping[str, object]], *, source: str, stream: str
) -> TradeFlow | None:
    matching = [
        event
        for event in events
        if str(event.get("source")) == source and str(event.get("stream")) == stream
    ]
    if not matching:
        return None

    received_events: list[tuple[datetime, Mapping[str, object]]] = []
    for event in matching:
        received = event.get("received_at")
        if not isinstance(received, datetime):
            raise TradeFlowError("raw event received_at must be a datetime")
        received_events.append((_stored_utc(received), event))
    coverage_cutoff, coverage_event = max(
        received_events,
        key=lambda item: (item[0], str(item[1].get("dedupe_key", ""))),
    )

    buy_volume = Decimal(0)
    sell_volume = Decimal(0)
    observations: list[dict[str, object]] = []
    trade_count = 0

    for received_at, event in received_events:
        for trade_index, (side, size, price) in enumerate(_trades(event, source=source)):
            if side == "BUY":
                buy_volume += size
            else:
                sell_volume += size
            trade_count += 1
            observations.append(
                {
                    "kind": "raw_trade",
                    "dedupe_key": str(event.get("dedupe_key", "")),
                    "source": source,
                    "stream": stream,
                    "event_type": str(event.get("event_type", "")),
                    "received_at": received_at,
                    "trade_index": trade_index,
                    "side": side,
                    "size": size,
                    "price": price,
                }
            )

    return TradeFlow(
        buy_volume=buy_volume,
        sell_volume=sell_volume,
        signed_volume=buy_volume - sell_volume,
        trade_count=trade_count,
        observations=tuple(observations),
        coverage_cutoff=coverage_cutoff,
        coverage_observation=_coverage_descriptor(
            coverage_event,
            source=source,
            stream=stream,
            received_at=coverage_cutoff,
        ),
    )


def load_trade_flow(
    connection: Connection,
    *,
    source: str,
    stream: str,
    instrument: str,
    feature_at: datetime,
    window_seconds: int = 60,
) -> TradeFlow | None:
    cutoff = _utc(feature_at, "feature_at")
    if window_seconds <= 0:
        raise ValueError("window_seconds must be positive")
    left = cutoff - timedelta(seconds=window_seconds)

    # raw_window_exclusion() is half-open; convert the query's (left, cutoff]
    # microsecond-resolution interval to [left + 1us, cutoff + 1us).
    query_start = left + timedelta(microseconds=1)
    query_end = cutoff + timedelta(microseconds=1)
    if raw_window_exclusion(query_start, query_end) is not None:
        return None

    rows = connection.execute(
        select(raw_market_events)
        .where(
            raw_market_events.c.source == source,
            raw_market_events.c.stream == stream,
            raw_market_events.c.instrument == instrument,
            raw_market_events.c.received_at > left,
            raw_market_events.c.received_at <= cutoff,
        )
        .order_by(raw_market_events.c.received_at, raw_market_events.c.id)
    ).mappings().all()
    return parse_trade_flow((dict(row) for row in rows), source=source, stream=stream)
