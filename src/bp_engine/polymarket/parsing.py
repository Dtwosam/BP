from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from bp_engine.polymarket.models import PolymarketMarket

_SLUG_RE = re.compile(r"^btc-updown-(?P<minutes>\d+)m-(?P<epoch>\d+)$")


class GammaMarketError(ValueError):
    """Raised when Gamma market metadata cannot be normalized safely."""


def parse_horizon_slug(slug: str) -> tuple[int, datetime]:
    match = _SLUG_RE.fullmatch(slug)
    if match is None:
        raise GammaMarketError(f"unsupported BTC Up/Down slug: {slug}")

    minutes = int(match.group("minutes"))
    if minutes <= 0:
        raise GammaMarketError(f"invalid horizon in slug: {slug}")

    seconds = minutes * 60
    start_at = datetime.fromtimestamp(int(match.group("epoch")), tz=UTC)
    return seconds, start_at


def _decode_string_array(payload: Mapping[str, Any], key: str) -> list[str]:
    raw = payload.get(key)
    if not isinstance(raw, str):
        raise GammaMarketError(f"{key} must be a JSON-encoded string array")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GammaMarketError(f"{key} is not valid JSON") from exc
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise GammaMarketError(f"{key} must decode to a string array")
    return value


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise GammaMarketError(f"{key} must be a non-empty string")
    return value


def _rule_hash(resolution_source: str, rules_text: str) -> str:
    normalized = f"{resolution_source.strip()}\n{rules_text.strip()}".encode()
    return f"sha256:{hashlib.sha256(normalized).hexdigest()}"


def _resolved_outcome(payload: Mapping[str, Any], outcomes: list[str]) -> str | None:
    if not bool(payload.get("closed")):
        return None
    try:
        prices = [Decimal(value) for value in _decode_string_array(payload, "outcomePrices")]
    except (GammaMarketError, InvalidOperation):
        return None
    if len(prices) != len(outcomes):
        return None

    winners = [outcome for outcome, price in zip(outcomes, prices, strict=True) if price == 1]
    losers = [price for price in prices if price == 0]
    if len(winners) == 1 and len(losers) == len(prices) - 1 and winners[0] in {"Up", "Down"}:
        return winners[0]
    return None


def parse_gamma_market(payload: Mapping[str, Any]) -> PolymarketMarket:
    slug = _required_string(payload, "slug")
    horizon_seconds, window_start_at = parse_horizon_slug(slug)

    outcomes = _decode_string_array(payload, "outcomes")
    token_ids = _decode_string_array(payload, "clobTokenIds")
    if len(outcomes) != len(token_ids):
        raise GammaMarketError("outcomes and clobTokenIds must have the same length")

    token_by_outcome = dict(zip(outcomes, token_ids, strict=True))
    if set(token_by_outcome) != {"Up", "Down"}:
        raise GammaMarketError("market must contain exactly Up and Down outcomes")

    resolution_source = _required_string(payload, "resolutionSource")
    rules_text = _required_string(payload, "description")

    events = payload.get("events")
    event_id = None
    if isinstance(events, list) and events and isinstance(events[0], Mapping):
        candidate = events[0].get("id")
        if isinstance(candidate, str) and candidate:
            event_id = candidate

    return PolymarketMarket(
        gamma_market_id=_required_string(payload, "id"),
        event_id=event_id,
        condition_id=_required_string(payload, "conditionId"),
        slug=slug,
        question=_required_string(payload, "question"),
        horizon_seconds=horizon_seconds,
        window_start_at=window_start_at,
        window_end_at=window_start_at + timedelta(seconds=horizon_seconds),
        up_token_id=token_by_outcome["Up"],
        down_token_id=token_by_outcome["Down"],
        resolution_source=resolution_source,
        rules_text=rules_text,
        rules_hash=_rule_hash(resolution_source, rules_text),
        active=bool(payload.get("active")),
        closed=bool(payload.get("closed")),
        accepting_orders=bool(payload.get("acceptingOrders")),
        resolved_outcome=_resolved_outcome(payload, outcomes),
    )
