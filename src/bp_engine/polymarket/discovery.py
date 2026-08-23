from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

from bp_engine.polymarket.models import PolymarketMarket
from bp_engine.polymarket.parsing import parse_gamma_market

_HORIZON_RE = re.compile(r"^(?P<minutes>[1-9]\d*)m$")


class GammaClientProtocol(Protocol):
    async def get_market_by_slug(self, slug: str) -> dict[str, Any] | None: ...


def _horizon_seconds(horizon: str) -> int:
    match = _HORIZON_RE.fullmatch(horizon)
    if match is None:
        raise ValueError(f"unsupported horizon format: {horizon}")
    return int(match.group("minutes")) * 60


def build_candidate_slugs(
    now: datetime,
    horizons: Sequence[str],
    offsets: Sequence[int] = (-1, 0, 1),
) -> list[str]:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    epoch_now = int(now.astimezone(UTC).timestamp())
    slugs: list[str] = []
    for horizon in horizons:
        seconds = _horizon_seconds(horizon)
        current_start = (epoch_now // seconds) * seconds
        for offset in offsets:
            start = current_start + (offset * seconds)
            slugs.append(f"btc-updown-{horizon}-{start}")
    return slugs


async def discover_btc_markets(
    client: GammaClientProtocol,
    now: datetime,
    horizons: Sequence[str],
    offsets: Sequence[int] = (-1, 0, 1),
) -> list[PolymarketMarket]:
    by_condition_id: dict[str, PolymarketMarket] = {}
    for slug in build_candidate_slugs(now, horizons=horizons, offsets=offsets):
        payload = await client.get_market_by_slug(slug)
        if payload is None:
            continue
        market = parse_gamma_market(payload)
        by_condition_id.setdefault(market.condition_id, market)

    return sorted(
        by_condition_id.values(),
        key=lambda market: (market.window_start_at, market.horizon_seconds),
    )
