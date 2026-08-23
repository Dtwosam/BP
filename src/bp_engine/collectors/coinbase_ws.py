from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from bp_engine.recorder.models import RawEvent


def build_coinbase_subscriptions(product_ids: Sequence[str]) -> list[dict[str, object]]:
    products = sorted({product_id for product_id in product_ids if product_id})
    if not products:
        raise ValueError("at least one Coinbase product id is required")
    return [
        {"type": "subscribe", "product_ids": products, "channel": "ticker"},
        {"type": "subscribe", "product_ids": products, "channel": "market_trades"},
        {"type": "subscribe", "channel": "heartbeats"},
    ]


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Coinbase timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def _first_event(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    events = payload.get("events")
    if not isinstance(events, list) or not events or not isinstance(events[0], Mapping):
        return None
    return events[0]


def _instrument(channel: str, event: Mapping[str, Any]) -> str | None:
    if channel in {"l2_data", "level2"}:
        product_id = event.get("product_id")
        return str(product_id) if product_id else None
    if channel == "ticker":
        tickers = event.get("tickers")
        if isinstance(tickers, list) and tickers and isinstance(tickers[0], Mapping):
            product_id = tickers[0].get("product_id")
            return str(product_id) if product_id else None
    if channel == "market_trades":
        trades = event.get("trades")
        if isinstance(trades, list) and trades and isinstance(trades[0], Mapping):
            product_id = trades[0].get("product_id")
            return str(product_id) if product_id else None
    return None


def parse_coinbase_message(
    payload: Mapping[str, Any],
    *,
    received_at: datetime,
) -> list[RawEvent]:
    channel = payload.get("channel")
    if not isinstance(channel, str) or channel in {"heartbeats", "subscriptions"}:
        return []
    if channel not in {"l2_data", "level2", "ticker", "market_trades"}:
        return []

    event = _first_event(payload)
    if event is None:
        return []
    instrument = _instrument(channel, event)
    if instrument is None:
        return []

    kind = str(event.get("type") or "update")
    if channel in {"l2_data", "level2"}:
        prefix = "level2"
    elif channel == "ticker":
        prefix = "ticker"
    else:
        prefix = "market_trades"
    return [
        RawEvent.build(
            source="coinbase",
            stream="spot",
            instrument=instrument,
            event_type=f"{prefix}_{kind}",
            source_timestamp=_timestamp(payload.get("timestamp")),
            received_at=received_at,
            sequence=payload.get("sequence_num"),
            payload=payload,
        )
    ]
