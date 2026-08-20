from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from bp_engine.recorder.models import RawEvent


class QueueBackpressure(RuntimeError):
    """Raised when a bounded event buffer cannot accept another event."""


class EventBuffer:
    """Bounded async queue that never silently drops market events."""

    def __init__(self, maxsize: int) -> None:
        if maxsize <= 0:
            raise ValueError("maxsize must be greater than zero")
        self._queue: asyncio.Queue[RawEvent] = asyncio.Queue(maxsize=maxsize)

    async def put(self, event: RawEvent) -> None:
        await self._queue.put(event)

    def put_nowait(self, event: RawEvent) -> None:
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull as exc:
            raise QueueBackpressure("event buffer is full") from exc

    async def get(self) -> RawEvent:
        return await self._queue.get()

    def get_nowait(self) -> RawEvent:
        return self._queue.get_nowait()

    def empty(self) -> bool:
        return self._queue.empty()

    def qsize(self) -> int:
        return self._queue.qsize()


Sink = Callable[[list[RawEvent]], Awaitable[None]]


class BatchWriter:
    """Flush buffered events in bounded batches and drain them on shutdown."""

    def __init__(
        self,
        *,
        buffer: EventBuffer,
        sink: Sink,
        batch_size: int,
        flush_interval_seconds: float,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than zero")
        if flush_interval_seconds <= 0:
            raise ValueError("flush_interval_seconds must be greater than zero")
        self._buffer = buffer
        self._sink = sink
        self._batch_size = batch_size
        self._flush_interval_seconds = flush_interval_seconds

    async def _flush(self, batch: list[RawEvent]) -> None:
        if not batch:
            return
        await self._sink(list(batch))
        batch.clear()

    async def _wait_for_event_or_stop(
        self, stop: asyncio.Event
    ) -> tuple[RawEvent | None, bool]:
        get_task = asyncio.create_task(self._buffer.get())
        stop_task = asyncio.create_task(stop.wait())
        done, pending = await asyncio.wait(
            {get_task, stop_task},
            timeout=self._flush_interval_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        if not done:
            return None, False
        if stop_task in done and stop_task.result():
            if get_task in done:
                return get_task.result(), True
            return None, True
        return get_task.result(), False

    async def run(self, stop: asyncio.Event) -> None:
        batch: list[RawEvent] = []

        while not stop.is_set():
            event, stopped = await self._wait_for_event_or_stop(stop)
            if event is None:
                await self._flush(batch)
                if stopped:
                    break
                continue

            batch.append(event)
            while len(batch) < self._batch_size:
                try:
                    batch.append(self._buffer.get_nowait())
                except asyncio.QueueEmpty:
                    break

            if len(batch) >= self._batch_size:
                await self._flush(batch)

        while not self._buffer.empty():
            batch.append(self._buffer.get_nowait())
            if len(batch) >= self._batch_size:
                await self._flush(batch)

        await self._flush(batch)
