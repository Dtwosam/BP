from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from bp_engine.polymarket.models import PolymarketMarket

Discovery = Callable[[datetime], Awaitable[list[PolymarketMarket]]]


@dataclass(frozen=True)
class SubscriptionDiff:
    added: frozenset[str]
    removed: frozenset[str]
    current: frozenset[str]
    active_added: frozenset[str] = frozenset()


class PolymarketSubscriptionCoordinator:
    """Track rotating Polymarket token subscriptions with a short expiry grace."""

    def __init__(self, discovery: Discovery, *, grace_seconds: float) -> None:
        if grace_seconds < 0:
            raise ValueError("grace_seconds must be non-negative")
        self._discovery = discovery
        self._grace = timedelta(seconds=grace_seconds)
        self._token_expiry: dict[str, datetime] = {}
        self._current: set[str] = set()

    async def refresh(self, now: datetime) -> SubscriptionDiff:
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        now = now.astimezone(UTC)

        markets = await self._discovery(now)
        active_tokens: set[str] = set()
        for market in markets:
            if not market.active:
                continue
            expiry = market.window_end_at.astimezone(UTC) + self._grace
            if expiry < now:
                continue
            token_ids = (market.up_token_id, market.down_token_id)
            if market.window_start_at.astimezone(UTC) <= now < market.window_end_at.astimezone(UTC):
                active_tokens.update(token_ids)
            for token_id in token_ids:
                existing = self._token_expiry.get(token_id)
                if existing is None or expiry > existing:
                    self._token_expiry[token_id] = expiry

        desired = {
            token_id
            for token_id, expiry in self._token_expiry.items()
            if expiry >= now
        }
        expired = [token_id for token_id, expiry in self._token_expiry.items() if expiry < now]
        for token_id in expired:
            del self._token_expiry[token_id]

        added = desired - self._current
        removed = self._current - desired
        self._current = desired
        return SubscriptionDiff(
            added=frozenset(added),
            removed=frozenset(removed),
            current=frozenset(desired),
            active_added=frozenset(added & active_tokens),
        )
