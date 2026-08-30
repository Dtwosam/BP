import asyncio
from datetime import UTC, datetime

import pytest

from bp_engine.config import Settings
from bp_engine.recorder.models import RawEvent
from bp_engine.recorder.service import _BufferedEventSink, build_default_recorder_service
from bp_engine.recorder.state import MarketStateSnapshot, MarketStateSnapshotter
from bp_engine.recorder.writer import EventBuffer


class FakeReducer:
    def __init__(self) -> None:
        self.calls: list[datetime] = []

    def snapshots(self, bucket_at: datetime) -> list[MarketStateSnapshot]:
        self.calls.append(bucket_at)
        return [
            MarketStateSnapshot(
                bucket_at=bucket_at,
                state_key="bybit/spot/BTCUSDT",
                source="bybit",
                stream="spot",
                instrument="BTCUSDT",
                last_event_at=datetime(2026, 8, 23, 20, 0, tzinfo=UTC),
                state={"best_bid": "64000"},
            )
        ]


@pytest.mark.asyncio
async def test_snapshotter_writes_periodically_and_flushes_on_stop() -> None:
    reducer = FakeReducer()
    writes: list[list[MarketStateSnapshot]] = []
    first_write = asyncio.Event()

    async def write_snapshots(snapshots: list[MarketStateSnapshot]) -> None:
        writes.append(snapshots)
        first_write.set()

    snapshotter = MarketStateSnapshotter(
        reducer=reducer,
        write_snapshots=write_snapshots,
        interval_seconds=0.01,
        now=lambda: datetime(2026, 8, 23, 20, 1, 2, 345_000, tzinfo=UTC),
    )
    stop = asyncio.Event()
    task = asyncio.create_task(snapshotter.run(stop))

    await asyncio.wait_for(first_write.wait(), timeout=1)
    stop.set()
    await asyncio.wait_for(task, timeout=1)

    assert len(writes) >= 2
    assert all(batch[0].bucket_at.microsecond == 0 for batch in writes)


@pytest.mark.asyncio
async def test_snapshotter_does_not_rewrite_state_past_stale_threshold() -> None:
    class MixedAgeReducer:
        def snapshots(self, bucket_at: datetime) -> list[MarketStateSnapshot]:
            return [
                MarketStateSnapshot(
                    bucket_at=bucket_at,
                    state_key="fresh",
                    source="polymarket",
                    stream="market",
                    instrument="btc-updown-5m",
                    last_event_at=datetime(2026, 8, 23, 20, 0, 55, tzinfo=UTC),
                    state={"best_bid": "0.49"},
                ),
                MarketStateSnapshot(
                    bucket_at=bucket_at,
                    state_key="stale",
                    source="polymarket",
                    stream="market",
                    instrument="btc-updown-expired",
                    last_event_at=datetime(2026, 8, 23, 19, 59, tzinfo=UTC),
                    state={"best_bid": "0.51"},
                ),
            ]

    writes: list[list[MarketStateSnapshot]] = []
    first_write = asyncio.Event()

    async def write_snapshots(snapshots: list[MarketStateSnapshot]) -> None:
        writes.append(snapshots)
        first_write.set()

    snapshotter = MarketStateSnapshotter(
        reducer=MixedAgeReducer(),
        write_snapshots=write_snapshots,
        interval_seconds=0.01,
        max_state_age_seconds=10.0,
        now=lambda: datetime(2026, 8, 23, 20, 1, tzinfo=UTC),
    )
    stop = asyncio.Event()
    task = asyncio.create_task(snapshotter.run(stop))

    await asyncio.wait_for(first_write.wait(), timeout=1)
    stop.set()
    await asyncio.wait_for(task, timeout=1)

    assert writes
    assert all([snapshot.state_key for snapshot in batch] == ["fresh"] for batch in writes)


@pytest.mark.asyncio
async def test_reducer_failure_records_incident_without_dropping_raw_event() -> None:
    class BrokenReducer:
        def observe(self, event: RawEvent) -> None:
            raise ValueError("bad state payload")

    incidents: list[object] = []

    async def record_incident(incident: object) -> None:
        incidents.append(incident)

    buffer = EventBuffer(maxsize=10)
    event_sink = _BufferedEventSink(
        buffer,
        record_incident,
        state_reducer=BrokenReducer(),
    )
    event = RawEvent.build(
        source="bybit",
        stream="spot",
        instrument="BTCUSDT",
        event_type="trade",
        source_timestamp=None,
        received_at=datetime(2026, 8, 23, 20, 2, tzinfo=UTC),
        payload={"data": []},
    )

    await event_sink(event)

    assert buffer.get_nowait() == event
    assert len(incidents) == 1
    incident = incidents[0]
    assert incident.incident_type == "state_reducer_error"
    assert incident.details["error_type"] == "ValueError"
    assert incident.details["message"] == "bad state payload"


def test_default_builder_includes_state_snapshotter(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'recorder.db'}")

    service = build_default_recorder_service(settings)

    assert "state_snapshotter" in service.component_names
