import asyncio
from datetime import UTC, datetime

import pytest

from bp_engine.recorder.models import RawEvent
from bp_engine.recorder.writer import BatchWriter, EventBuffer, QueueBackpressure


def event(sequence: int) -> RawEvent:
    return RawEvent.build(
        source="bybit",
        stream="spot",
        instrument="BTCUSDT",
        event_type="trade",
        source_timestamp=datetime(2026, 8, 20, 21, 30, tzinfo=UTC),
        received_at=datetime(2026, 8, 20, 21, 30, tzinfo=UTC),
        sequence=sequence,
        payload={"seq": sequence},
    )


def test_event_buffer_surfaces_backpressure_instead_of_dropping() -> None:
    buffer = EventBuffer(maxsize=1)
    buffer.put_nowait(event(1))

    with pytest.raises(QueueBackpressure):
        buffer.put_nowait(event(2))


def test_batch_writer_requires_at_least_one_worker() -> None:
    buffer = EventBuffer(maxsize=10)

    async def sink(items: list[RawEvent]) -> None:
        return None

    with pytest.raises(ValueError, match="worker_count"):
        BatchWriter(
            buffer=buffer,
            sink=sink,
            batch_size=2,
            flush_interval_seconds=1,
            worker_count=0,
        )


@pytest.mark.asyncio
async def test_batch_writer_uses_bounded_parallel_workers() -> None:
    buffer = EventBuffer(maxsize=20)
    entered = 0
    max_entered = 0
    two_entered = asyncio.Event()
    release = asyncio.Event()

    async def sink(items: list[RawEvent]) -> None:
        nonlocal entered, max_entered
        entered += 1
        max_entered = max(max_entered, entered)
        if entered >= 2:
            two_entered.set()
        await release.wait()
        entered -= 1

    writer = BatchWriter(
        buffer=buffer,
        sink=sink,
        batch_size=1,
        flush_interval_seconds=1,
        worker_count=2,
    )
    stop = asyncio.Event()
    task = asyncio.create_task(writer.run(stop))

    await buffer.put(event(1))
    await buffer.put(event(2))
    await asyncio.wait_for(two_entered.wait(), timeout=1)
    release.set()
    stop.set()
    await asyncio.wait_for(task, timeout=1)

    assert max_entered == 2


@pytest.mark.asyncio
async def test_parallel_writer_drains_every_unique_event_on_shutdown() -> None:
    buffer = EventBuffer(maxsize=200)
    stored: list[str] = []

    async def sink(items: list[RawEvent]) -> None:
        await asyncio.sleep(0)
        stored.extend(str(item.sequence) for item in items)

    writer = BatchWriter(
        buffer=buffer,
        sink=sink,
        batch_size=7,
        flush_interval_seconds=0.01,
        worker_count=4,
    )
    for sequence in range(100):
        await buffer.put(event(sequence))

    stop = asyncio.Event()
    stop.set()
    await writer.run(stop)

    assert len(stored) == 100
    assert set(stored) == {str(sequence) for sequence in range(100)}


@pytest.mark.asyncio
async def test_parallel_writer_propagates_worker_failure() -> None:
    buffer = EventBuffer(maxsize=10)

    async def sink(items: list[RawEvent]) -> None:
        raise RuntimeError("database write failed")

    writer = BatchWriter(
        buffer=buffer,
        sink=sink,
        batch_size=1,
        flush_interval_seconds=1,
        worker_count=2,
    )
    await buffer.put(event(1))

    with pytest.raises(RuntimeError, match="database write failed"):
        await writer.run(asyncio.Event())


@pytest.mark.asyncio
async def test_batch_writer_flushes_when_batch_size_is_reached() -> None:
    buffer = EventBuffer(maxsize=10)
    batches: list[list[RawEvent]] = []
    flushed = asyncio.Event()

    async def sink(items: list[RawEvent]) -> None:
        batches.append(items)
        flushed.set()

    writer = BatchWriter(buffer=buffer, sink=sink, batch_size=2, flush_interval_seconds=10)
    stop = asyncio.Event()
    task = asyncio.create_task(writer.run(stop))

    await buffer.put(event(1))
    await buffer.put(event(2))
    await asyncio.wait_for(flushed.wait(), timeout=1)
    stop.set()
    await asyncio.wait_for(task, timeout=1)

    assert [[item.sequence for item in batch] for batch in batches] == [["1", "2"]]


@pytest.mark.asyncio
async def test_batch_writer_flushes_partial_batch_on_interval() -> None:
    buffer = EventBuffer(maxsize=10)
    batches: list[list[RawEvent]] = []
    flushed = asyncio.Event()

    async def sink(items: list[RawEvent]) -> None:
        batches.append(items)
        flushed.set()

    writer = BatchWriter(buffer=buffer, sink=sink, batch_size=10, flush_interval_seconds=0.01)
    stop = asyncio.Event()
    task = asyncio.create_task(writer.run(stop))

    await buffer.put(event(1))
    await asyncio.wait_for(flushed.wait(), timeout=1)
    stop.set()
    await asyncio.wait_for(task, timeout=1)

    assert len(batches) == 1
    assert batches[0][0].sequence == "1"


@pytest.mark.asyncio
async def test_batch_writer_drains_buffer_during_graceful_shutdown() -> None:
    buffer = EventBuffer(maxsize=10)
    batches: list[list[RawEvent]] = []

    async def sink(items: list[RawEvent]) -> None:
        batches.append(items)

    for sequence in (1, 2, 3):
        await buffer.put(event(sequence))

    stop = asyncio.Event()
    stop.set()
    writer = BatchWriter(buffer=buffer, sink=sink, batch_size=2, flush_interval_seconds=1)

    await writer.run(stop)

    assert [item.sequence for batch in batches for item in batch] == ["1", "2", "3"]
