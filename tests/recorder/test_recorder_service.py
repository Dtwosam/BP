import asyncio
from datetime import UTC, datetime

import pytest

from bp_engine.recorder.polymarket_coordinator import SubscriptionDiff
from bp_engine.recorder.service import (
    PolymarketCollectorComponent,
    RecorderService,
)


class FakeComponent:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()
        self.error = error

    async def run(self, stop: asyncio.Event) -> None:
        self.started.set()
        if self.error is not None:
            raise self.error
        await stop.wait()
        self.stopped.set()


@pytest.mark.asyncio
async def test_service_starts_all_components_and_stops_them_together() -> None:
    first = FakeComponent()
    second = FakeComponent()
    service = RecorderService({"first": first, "second": second})
    stop = asyncio.Event()

    task = asyncio.create_task(service.run(stop))
    await asyncio.wait_for(first.started.wait(), timeout=1)
    await asyncio.wait_for(second.started.wait(), timeout=1)
    stop.set()
    await asyncio.wait_for(task, timeout=1)

    assert first.stopped.is_set()
    assert second.stopped.is_set()


@pytest.mark.asyncio
async def test_service_propagates_component_failure_and_stops_siblings() -> None:
    healthy = FakeComponent()
    broken = FakeComponent(error=RuntimeError("feed failed"))
    service = RecorderService({"healthy": healthy, "broken": broken})
    stop = asyncio.Event()

    with pytest.raises(RuntimeError, match="feed failed"):
        await asyncio.wait_for(service.run(stop), timeout=1)

    assert stop.is_set()


class FakeCoordinator:
    def __init__(self) -> None:
        self.calls = 0

    async def refresh(self, now: datetime) -> SubscriptionDiff:
        self.calls += 1
        if self.calls == 1:
            return SubscriptionDiff(
                added=frozenset({"up-a", "down-a"}),
                removed=frozenset(),
                current=frozenset({"up-a", "down-a"}),
            )
        return SubscriptionDiff(
            added=frozenset({"up-b", "down-b"}),
            removed=frozenset({"up-a", "down-a"}),
            current=frozenset({"up-b", "down-b"}),
        )


class FakeRunner:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def run(self, stop: asyncio.Event) -> None:
        self.started.set()
        await stop.wait()


@pytest.mark.asyncio
async def test_polymarket_component_bootstraps_then_queues_rotation_updates() -> None:
    coordinator = FakeCoordinator()
    runner = FakeRunner()
    initial_assets: list[frozenset[str]] = []
    outbound: asyncio.Queue[object] = asyncio.Queue()

    def runner_factory(
        assets: frozenset[str], queue: asyncio.Queue[object]
    ) -> FakeRunner:
        assert queue is outbound
        initial_assets.append(assets)
        return runner

    component = PolymarketCollectorComponent(
        coordinator=coordinator,
        runner_factory=runner_factory,
        outbound_messages=outbound,
        refresh_interval_seconds=0.01,
        now=lambda: datetime(2026, 8, 20, 22, 30, tzinfo=UTC),
    )
    stop = asyncio.Event()
    task = asyncio.create_task(component.run(stop))

    await asyncio.wait_for(runner.started.wait(), timeout=1)
    for _ in range(100):
        if outbound.qsize() >= 2:
            break
        await asyncio.sleep(0.002)
    stop.set()
    await asyncio.wait_for(task, timeout=1)

    assert initial_assets == [frozenset({"up-a", "down-a"})]
    updates = [outbound.get_nowait(), outbound.get_nowait()]
    assert updates == [
        {"operation": "subscribe", "assets_ids": ["down-b", "up-b"]},
        {"operation": "unsubscribe", "assets_ids": ["down-a", "up-a"]},
    ]


@pytest.mark.asyncio
async def test_polymarket_component_refuses_empty_initial_subscription() -> None:
    class EmptyCoordinator:
        async def refresh(self, now: datetime) -> SubscriptionDiff:
            return SubscriptionDiff(frozenset(), frozenset(), frozenset())

    component = PolymarketCollectorComponent(
        coordinator=EmptyCoordinator(),
        runner_factory=lambda assets, queue: FakeRunner(),
        outbound_messages=asyncio.Queue(),
        refresh_interval_seconds=1,
        now=lambda: datetime(2026, 8, 20, 22, 30, tzinfo=UTC),
    )

    with pytest.raises(RuntimeError, match="no Polymarket assets"):
        await component.run(asyncio.Event())


def test_default_builder_assembles_primary_recorder_components_without_network(tmp_path) -> None:
    from bp_engine.config import Settings
    from bp_engine.recorder.service import build_default_recorder_service

    settings = Settings(database_url=f"sqlite:///{tmp_path / 'recorder.db'}")

    service = build_default_recorder_service(settings)

    assert service.component_names == frozenset(
        {"writer", "polymarket", "bybit_spot", "bybit_linear", "coinbase_spot"}
    )
    assert settings.live_trading_enabled is False
