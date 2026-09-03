import asyncio
from datetime import UTC, datetime

import pytest

from bp_engine.recorder.models import FeedIncident, RawEvent
from bp_engine.recorder.polymarket_coordinator import SubscriptionDiff
from bp_engine.recorder.service import (
    PolymarketCollectorComponent,
    RecorderService,
    _BufferedEventSink,
)
from bp_engine.recorder.writer import EventBuffer


def raw_event(sequence: int, second: int) -> RawEvent:
    observed_at = datetime(2026, 9, 3, 17, 0, second, tzinfo=UTC)
    return RawEvent.build(
        source="polymarket",
        stream="market",
        instrument="condition-test",
        event_type="last_trade_price",
        source_timestamp=observed_at,
        received_at=observed_at,
        sequence=sequence,
        asset_id="token-test",
        payload={"asset_id": "token-test", "price": "0.5"},
    )


async def let_blocked_sinks_reach_queue_wait(*tasks: asyncio.Task[None]) -> None:
    for _ in range(100):
        if all(not task.done() for task in tasks):
            await asyncio.sleep(0)
            if all(not task.done() for task in tasks):
                return
        await asyncio.sleep(0)
    raise AssertionError("event sinks did not remain blocked on the full queue")


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


@pytest.mark.asyncio
async def test_backpressure_is_coalesced_and_recovery_preserves_every_event() -> None:
    buffer = EventBuffer(maxsize=1)
    incidents: list[FeedIncident] = []
    sink = _BufferedEventSink(buffer, incidents.append)
    buffer.put_nowait(raw_event(0, 0))

    first = asyncio.create_task(sink(raw_event(1, 1)))
    second = asyncio.create_task(sink(raw_event(2, 2)))
    await let_blocked_sinks_reach_queue_wait(first, second)

    drained = [(await buffer.get()).sequence]
    await asyncio.sleep(0)
    drained.append((await buffer.get()).sequence)
    await asyncio.gather(first, second)
    drained.append((await buffer.get()).sequence)

    await sink(raw_event(3, 3))
    drained.append((await buffer.get()).sequence)

    assert set(drained) == {"0", "1", "2", "3"}
    assert [incident.incident_type for incident in incidents] == [
        "backpressure",
        "backpressure_recovered",
    ]
    assert incidents[0].details["queue_size"] == 1
    assert incidents[0].details["episode_started_at"] == "2026-09-03T17:00:01+00:00"
    assert incidents[1].details["episode_started_at"] == "2026-09-03T17:00:01+00:00"
    assert incidents[1].details["recovered_at"] == "2026-09-03T17:00:03+00:00"
    assert incidents[1].details["blocked_event_count"] == 2
    assert incidents[1].details["duration_seconds"] == 2.0


@pytest.mark.asyncio
async def test_later_backpressure_creates_a_distinct_episode() -> None:
    buffer = EventBuffer(maxsize=1)
    incidents: list[FeedIncident] = []
    sink = _BufferedEventSink(buffer, incidents.append)

    async def run_episode(
        fill_sequence: int,
        blocked_sequence: int,
        recovery_sequence: int,
    ) -> None:
        buffer.put_nowait(raw_event(fill_sequence, fill_sequence))
        blocked = asyncio.create_task(sink(raw_event(blocked_sequence, blocked_sequence)))
        await let_blocked_sinks_reach_queue_wait(blocked)
        await buffer.get()
        await blocked
        await buffer.get()
        await sink(raw_event(recovery_sequence, recovery_sequence))
        await buffer.get()

    await run_episode(0, 1, 2)
    await run_episode(3, 4, 5)

    assert [incident.incident_type for incident in incidents] == [
        "backpressure",
        "backpressure_recovered",
        "backpressure",
        "backpressure_recovered",
    ]
    assert incidents[1].details["blocked_event_count"] == 1
    assert incidents[3].details["blocked_event_count"] == 1


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

    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'recorder.db'}",
        recorder_writer_workers=3,
    )

    service = build_default_recorder_service(settings)

    assert service.component_names == frozenset(
        {
            "writer",
            "state_snapshotter",
            "polymarket",
            "bybit_spot",
            "bybit_linear",
            "coinbase_spot",
        }
    )
    writer_component = service._components["writer"]
    assert writer_component._writer._worker_count == 3
    assert settings.live_trading_enabled is False
