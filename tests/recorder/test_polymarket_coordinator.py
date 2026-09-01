from datetime import UTC, datetime, timedelta

import pytest

from bp_engine.polymarket.models import PolymarketMarket
from bp_engine.recorder.polymarket_coordinator import PolymarketSubscriptionCoordinator


def market(
    *,
    condition: str,
    start: datetime,
    horizon_seconds: int = 300,
    up: str | None = None,
    down: str | None = None,
) -> PolymarketMarket:
    return PolymarketMarket(
        gamma_market_id=f"market-{condition}",
        event_id=f"event-{condition}",
        condition_id=condition,
        slug=f"btc-updown-{horizon_seconds // 60}m-{int(start.timestamp())}",
        question="Bitcoin Up or Down",
        horizon_seconds=horizon_seconds,
        window_start_at=start,
        window_end_at=start + timedelta(seconds=horizon_seconds),
        up_token_id=up or f"up-{condition}",
        down_token_id=down or f"down-{condition}",
        resolution_source="chainlink",
        rules_text="rule",
        rules_hash="sha256:rule",
        active=True,
        closed=False,
        accepting_orders=True,
    )


class FakeDiscovery:
    def __init__(self, snapshots: list[list[PolymarketMarket]]) -> None:
        self.snapshots = snapshots
        self.calls: list[datetime] = []

    async def __call__(self, now: datetime) -> list[PolymarketMarket]:
        self.calls.append(now)
        return self.snapshots.pop(0)


@pytest.mark.asyncio
async def test_first_refresh_adds_both_tokens_for_discovered_market() -> None:
    now = datetime(2026, 8, 20, 22, 0, tzinfo=UTC)
    discovery = FakeDiscovery([[market(condition="a", start=now)]])
    coordinator = PolymarketSubscriptionCoordinator(discovery, grace_seconds=30)

    diff = await coordinator.refresh(now)

    assert diff.added == frozenset({"up-a", "down-a"})
    assert diff.removed == frozenset()
    assert diff.current == frozenset({"up-a", "down-a"})


@pytest.mark.asyncio
async def test_refresh_adds_new_market_without_duplicating_shared_token() -> None:
    now = datetime(2026, 8, 20, 22, 0, tzinfo=UTC)
    first = market(condition="a", start=now, up="shared", down="down-a")
    second = market(
        condition="b",
        start=now + timedelta(minutes=5),
        up="shared",
        down="down-b",
    )
    discovery = FakeDiscovery([[first], [first, second]])
    coordinator = PolymarketSubscriptionCoordinator(discovery, grace_seconds=30)

    await coordinator.refresh(now)
    diff = await coordinator.refresh(now + timedelta(minutes=1))

    assert diff.added == frozenset({"down-b"})
    assert diff.current == frozenset({"shared", "down-a", "down-b"})


@pytest.mark.asyncio
async def test_refresh_marks_newly_added_in_window_tokens_as_active_additions() -> None:
    now = datetime(2026, 8, 20, 22, 0, tzinfo=UTC)
    established = market(condition="established", start=now, horizon_seconds=900)
    late = market(condition="late", start=now + timedelta(minutes=5))
    discovery = FakeDiscovery([[established], [established, late]])
    coordinator = PolymarketSubscriptionCoordinator(discovery, grace_seconds=30)

    await coordinator.refresh(now)
    diff = await coordinator.refresh(now + timedelta(minutes=6))

    assert diff.added == frozenset({"up-late", "down-late"})
    assert diff.active_added == frozenset({"up-late", "down-late"})


@pytest.mark.asyncio
async def test_rotation_keeps_old_tokens_until_grace_expires_then_removes_them() -> None:
    start = datetime(2026, 8, 20, 22, 0, tzinfo=UTC)
    old = market(condition="old", start=start)
    new = market(condition="new", start=start + timedelta(minutes=5))
    discovery = FakeDiscovery([[old], [new], [new]])
    coordinator = PolymarketSubscriptionCoordinator(discovery, grace_seconds=30)

    await coordinator.refresh(start)
    within_grace = await coordinator.refresh(start + timedelta(minutes=5, seconds=15))
    after_grace = await coordinator.refresh(start + timedelta(minutes=5, seconds=31))

    assert within_grace.removed == frozenset()
    assert within_grace.added == frozenset({"up-new", "down-new"})
    assert after_grace.removed == frozenset({"up-old", "down-old"})
    assert after_grace.current == frozenset({"up-new", "down-new"})


@pytest.mark.asyncio
async def test_refresh_rejects_naive_time() -> None:
    discovery = FakeDiscovery([[]])
    coordinator = PolymarketSubscriptionCoordinator(discovery, grace_seconds=30)

    with pytest.raises(ValueError, match="timezone-aware"):
        await coordinator.refresh(datetime(2026, 8, 20, 22, 0))
