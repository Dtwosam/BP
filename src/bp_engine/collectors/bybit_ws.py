from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, Mapping, Sequence

from bp_engine.recorder.models import RawEvent

BybitVenue = Literal["spot", "linear"]


def build_bybit_subscription(topics: Sequence[str]) -> dict[str, object]:
    normalized = sorted({topic for topic in topics if topic})
    if not normalized:
        raise ValueError("at least one Bybit topic is required")
    return {"op": "subscribe", "args": normalized}


def _milliseconds(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    return datetime.fromtimestamp(int(str(value)) / 1000, tz=UTC)


def _instrument(payload: Mapping[str, Any]) -> str:
    data = payload.get("data")
    if isinstance(data, Mapping):
        value = data.get("s") or data.get("symbol")
        if value:
            return str(value)
    if isinstance(data, list) and data and isinstance(data[0], Mapping):
        value = data[0].get("s") or data[0].get("symbol")
        if value:
            return str(value)
    topic = payload.get("topic")
    if isinstance(topic, str) and "." in topic:
        return topic.rsplit(".", 1)[-1]
    return "BTCUSDT"


def _sequence(payload: Mapping[str, Any]) -> str | int | None:
    topic = str(payload.get("topic", ""))
    data = payload.get("data")
    if topic.startswith("orderbook.") and isinstance(data, Mapping):
        return data.get("seq")
    if topic.startswith("tickers."):
        return payload.get("cs")
    if topic.startswith("publicTrade.") and isinstance(data, list) and data:
        last = data[-1]
        if isinstance(last, Mapping):
            return last.get("seq")
    return None


def _event_type(payload: Mapping[str, Any]) -> str | None:
    topic = str(payload.get("topic", ""))
    if topic.startswith("orderbook."):
        message_type = str(payload.get("type", "snapshot"))
        return f"orderbook_{message_type}"
    if topic.startswith("publicTrade."):
        return "trade"
    if topic.startswith("tickers."):
        return "ticker"
    if topic.startswith("allLiquidation."):
        return "liquidation"
    return None


def parse_bybit_message(
    payload: Mapping[str, Any],
    *,
    venue: BybitVenue,
    received_at: datetime,
) -> list[RawEvent]:
    if payload.get("op") is not None or payload.get("ret_msg") == "pong":
        return []

    event_type = _event_type(payload)
    if event_type is None:
        return []

    source_time_value = (
        payload.get("cts") if event_type.startswith("orderbook_") else payload.get("ts")
    )
    return [
        RawEvent.build(
            source="bybit",
            stream=venue,
            instrument=_instrument(payload),
            event_type=event_type,
            source_timestamp=_milliseconds(source_time_value),
            received_at=received_at,
            sequence=_sequence(payload),
            payload=payload,
        )
    ]
