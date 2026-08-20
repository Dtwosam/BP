from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from bp_engine.recorder.models import RawEvent


def _asset_ids(asset_ids: Sequence[str]) -> list[str]:
    normalized = sorted({asset_id for asset_id in asset_ids if asset_id})
    if not normalized:
        raise ValueError("at least one asset id is required")
    return normalized


def build_market_subscription(asset_ids: Sequence[str]) -> dict[str, object]:
    return {"assets_ids": _asset_ids(asset_ids), "type": "market"}


def build_subscription_update(
    operation: Literal["subscribe", "unsubscribe"],
    asset_ids: Sequence[str],
) -> dict[str, object]:
    if operation not in {"subscribe", "unsubscribe"}:
        raise ValueError("operation must be subscribe or unsubscribe")
    return {"operation": operation, "assets_ids": _asset_ids(asset_ids)}


def _source_timestamp(payload: Mapping[str, Any]) -> datetime | None:
    value = payload.get("timestamp")
    if value in (None, ""):
        return None
    try:
        milliseconds = int(str(value))
    except ValueError as exc:
        raise ValueError("Polymarket timestamp must be milliseconds since epoch") from exc
    return datetime.fromtimestamp(milliseconds / 1000, tz=UTC)


def _parse_one(payload: Mapping[str, Any], received_at: datetime) -> RawEvent | None:
    event_type = payload.get("event_type")
    if not isinstance(event_type, str) or not event_type:
        return None

    market_id = payload.get("market")
    asset_id = payload.get("asset_id")
    instrument = str(market_id or asset_id or "polymarket-market")

    return RawEvent.build(
        source="polymarket",
        stream="market",
        instrument=instrument,
        event_type=event_type,
        source_timestamp=_source_timestamp(payload),
        received_at=received_at,
        market_id=str(market_id) if market_id is not None else None,
        asset_id=str(asset_id) if asset_id is not None else None,
        payload=payload,
    )


def parse_polymarket_message(
    message: Mapping[str, Any] | Sequence[Mapping[str, Any]] | str,
    *,
    received_at: datetime,
) -> list[RawEvent]:
    if isinstance(message, str):
        if message.upper() == "PONG":
            return []
        raise ValueError("unexpected Polymarket text message")

    payloads: Sequence[Mapping[str, Any]]
    if isinstance(message, Mapping):
        payloads = [message]
    else:
        payloads = message

    events: list[RawEvent] = []
    for payload in payloads:
        event = _parse_one(payload, received_at)
        if event is not None:
            events.append(event)
    return events
