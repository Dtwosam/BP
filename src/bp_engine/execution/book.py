from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import Connection, and_, or_, select

from bp_engine.storage.schema import raw_market_events

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class BookReplayError(RuntimeError):
    """Raised when raw book evidence cannot be replayed unambiguously."""


@dataclass(frozen=True, order=True)
class BookLevel:
    price: Decimal
    size: Decimal


@dataclass(frozen=True)
class ReplayedBook:
    condition_id: str
    asset_id: str
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    anchor_event_id: int
    anchor_dedupe_key: str
    applied_event_ids: tuple[int, ...]
    applied_dedupe_keys: tuple[str, ...]
    replay_cutoff_at: datetime


AppliedPayload = tuple[int, str, Mapping[str, Any]]


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise BookReplayError("replay cutoff must be timezone-aware")
    return value.astimezone(UTC)


def _text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise BookReplayError(f"{name} must be a non-empty string")
    return value


def _dedupe(value: str) -> str:
    if _SHA256_RE.fullmatch(value) is None:
        raise BookReplayError("raw event dedupe key must be a lowercase SHA-256 digest")
    return value


def _decimal(value: object, *, name: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise BookReplayError(f"{name} must be numeric")
    try:
        numeric = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise BookReplayError(f"{name} must be numeric") from exc
    if not numeric.is_finite():
        raise BookReplayError(f"{name} must be finite")
    return numeric


def _price(value: object) -> Decimal:
    numeric = _decimal(value, name="price")
    if not Decimal("0") <= numeric <= Decimal("1"):
        raise BookReplayError("price must be within [0, 1]")
    return numeric


def _size(value: object) -> Decimal:
    numeric = _decimal(value, name="size")
    if numeric < 0:
        raise BookReplayError("size must be non-negative")
    return numeric


def _level(level: object) -> tuple[Decimal, Decimal]:
    if isinstance(level, Mapping):
        if "price" not in level or "size" not in level:
            raise BookReplayError("book level requires price and size")
        return _price(level["price"]), _size(level["size"])
    if isinstance(level, Sequence) and not isinstance(level, (str, bytes, bytearray)):
        if len(level) < 2:
            raise BookReplayError("book level requires price and size")
        return _price(level[0]), _size(level[1])
    raise BookReplayError("book level must be an object or pair")


def _levels(payload: object, *, side: str) -> dict[Decimal, Decimal]:
    if not isinstance(payload, list):
        raise BookReplayError(f"{side} levels must be a list")
    result: dict[Decimal, Decimal] = {}
    for raw_level in payload:
        price, size = _level(raw_level)
        if price in result:
            raise BookReplayError(f"duplicate {side} price level")
        if size > 0:
            result[price] = size
    return result


def _validate_market(
    payload: Mapping[str, Any],
    *,
    condition_id: str,
    event_type: str,
) -> None:
    if payload.get("event_type") != event_type:
        raise BookReplayError(f"expected {event_type} payload")
    if _text(payload.get("market"), name="market") != condition_id:
        raise BookReplayError("raw event market conflicts with requested condition")


def _validate_not_crossed(
    bids: Mapping[Decimal, Decimal],
    asks: Mapping[Decimal, Decimal],
) -> None:
    if not bids or not asks:
        return
    if max(bids) >= min(asks):
        raise BookReplayError("replayed book is crossed or locked")


def _apply_changes(
    bids: dict[Decimal, Decimal],
    asks: dict[Decimal, Decimal],
    payload: Mapping[str, Any],
    *,
    condition_id: str,
    asset_id: str,
) -> bool:
    _validate_market(payload, condition_id=condition_id, event_type="price_change")
    changes = payload.get("price_changes")
    if not isinstance(changes, list):
        raise BookReplayError("price_change payload requires price_changes list")

    used = False
    for change in changes:
        if not isinstance(change, Mapping):
            raise BookReplayError("price_change entry must be an object")
        if str(change.get("asset_id") or "") != asset_id:
            continue
        used = True
        side = str(change.get("side") or "").upper()
        if side not in {"BUY", "SELL"}:
            raise BookReplayError("price_change side must be BUY or SELL")
        price = _price(change.get("price"))
        size = _size(change.get("size"))
        levels = bids if side == "BUY" else asks
        if size == 0:
            levels.pop(price, None)
        else:
            levels[price] = size

    if used:
        _validate_not_crossed(bids, asks)
    return used


def replay_book_payloads(
    *,
    condition_id: str,
    asset_id: str,
    anchor_event_id: int,
    anchor_dedupe_key: str,
    anchor_payload: Mapping[str, Any],
    applied_events: Sequence[AppliedPayload],
    replay_cutoff_at: datetime,
) -> ReplayedBook:
    if anchor_event_id <= 0:
        raise BookReplayError("anchor event id must be positive")
    _dedupe(anchor_dedupe_key)
    cutoff = _aware_utc(replay_cutoff_at)
    _validate_market(anchor_payload, condition_id=condition_id, event_type="book")
    if _text(anchor_payload.get("asset_id"), name="asset_id") != asset_id:
        raise BookReplayError("book anchor asset conflicts with requested token")

    bids = _levels(anchor_payload.get("bids"), side="bid")
    asks = _levels(anchor_payload.get("asks"), side="ask")
    _validate_not_crossed(bids, asks)

    used_ids: list[int] = []
    used_keys: list[str] = []
    previous_event_id = anchor_event_id
    for event_id, dedupe_key, payload in applied_events:
        if event_id <= 0:
            raise BookReplayError("applied event id must be positive")
        if event_id == previous_event_id:
            raise BookReplayError("duplicate applied event id")
        previous_event_id = event_id
        key = _dedupe(dedupe_key)
        if _apply_changes(
            bids,
            asks,
            payload,
            condition_id=condition_id,
            asset_id=asset_id,
        ):
            used_ids.append(event_id)
            used_keys.append(key)

    return ReplayedBook(
        condition_id=condition_id,
        asset_id=asset_id,
        bids=tuple(
            BookLevel(price=price, size=size)
            for price, size in sorted(bids.items(), reverse=True)
        ),
        asks=tuple(
            BookLevel(price=price, size=size)
            for price, size in sorted(asks.items())
        ),
        anchor_event_id=anchor_event_id,
        anchor_dedupe_key=anchor_dedupe_key,
        applied_event_ids=tuple(used_ids),
        applied_dedupe_keys=tuple(used_keys),
        replay_cutoff_at=cutoff,
    )


class PolymarketBookReplayReader:
    """Reconstruct a selected Polymarket token book using only causal raw evidence."""

    def book_at(
        self,
        connection: Connection,
        *,
        condition_id: str,
        asset_id: str,
        observed_at: datetime,
    ) -> ReplayedBook | None:
        cutoff = _aware_utc(observed_at)
        anchor = connection.execute(
            select(raw_market_events)
            .where(
                raw_market_events.c.source == "polymarket",
                raw_market_events.c.stream == "market",
                raw_market_events.c.instrument == condition_id,
                raw_market_events.c.event_type == "book",
                raw_market_events.c.asset_id == asset_id,
                raw_market_events.c.received_at <= cutoff,
            )
            .order_by(
                raw_market_events.c.received_at.desc(),
                raw_market_events.c.id.desc(),
            )
            .limit(1)
        ).mappings().one_or_none()
        if anchor is None:
            return None

        later_than_anchor = or_(
            raw_market_events.c.received_at > anchor["received_at"],
            and_(
                raw_market_events.c.received_at == anchor["received_at"],
                raw_market_events.c.id > anchor["id"],
            ),
        )
        rows = connection.execute(
            select(
                raw_market_events.c.id,
                raw_market_events.c.dedupe_key,
                raw_market_events.c.payload,
            )
            .where(
                raw_market_events.c.source == "polymarket",
                raw_market_events.c.stream == "market",
                raw_market_events.c.instrument == condition_id,
                raw_market_events.c.event_type == "price_change",
                later_than_anchor,
                raw_market_events.c.received_at <= cutoff,
            )
            .order_by(raw_market_events.c.received_at, raw_market_events.c.id)
        ).mappings().all()

        return replay_book_payloads(
            condition_id=condition_id,
            asset_id=asset_id,
            anchor_event_id=int(anchor["id"]),
            anchor_dedupe_key=str(anchor["dedupe_key"]),
            anchor_payload=anchor["payload"],
            applied_events=tuple(
                (int(row["id"]), str(row["dedupe_key"]), row["payload"])
                for row in rows
            ),
            replay_cutoff_at=cutoff,
        )
