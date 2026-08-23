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
