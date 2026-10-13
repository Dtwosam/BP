"""Fully offline integration contracts for the DORMANT Stage B1 bulk observer."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select

from bp_engine.collectors.bybit_ws import parse_bybit_message
from bp_engine.collectors.websocket_runner import WebSocketCollectorRunner
from bp_engine.recorder.models import RawEvent
from bp_engine.recorder.service import _BufferedEventSink, _RoutedBufferedEventSink
from bp_engine.recorder.stage_b1_observer import (
    MAX_DURATION_SECONDS,
    StageB1BulkObserver,
)
from bp_engine.recorder.writer import BatchWriter, EventBuffer
from bp_engine.storage.recorder import RecorderRepository
from bp_engine.storage.schema import metadata, raw_market_events


def _trade(when: datetime, price: str = "60001") -> dict[str, object]:
    return {
        "topic": "publicTrade.BTCUSDT",
        "ts": int(when.timestamp() * 1000),
        "data": [{"s": "BTCUSDT", "p": price, "T": int(when.timestamp() * 1000)}],
    }


def _book(when: datetime) -> dict[str, object]:
    return {
        "topic": "orderbook.50.BTCUSDT",
        "type": "delta",
        "cts": int(when.timestamp() * 1000),
        "data": {"s": "BTCUSDT", "b": [], "a": []},
    }


def _parser(message: object, received_at: datetime) -> list[RawEvent]:
    assert isinstance(message, dict)
    return parse_bybit_message(message, venue="linear", received_at=received_at)


def test_b1_requires_finite_duration_and_stops_collecting_after_expiry() -> None:
    for invalid in (0, -1, 121, 1.5, True):
        with pytest.raises(ValueError, match="1 to 120"):
            StageB1BulkObserver(duration_seconds=invalid)

    ticks = [100.0]
    observer = StageB1BulkObserver(
        duration_seconds=4, monotonic=lambda: ticks[0]
    )
    when = datetime(2026, 10, 13, 11, tzinfo=UTC)
    wrapped = observer.wrap_parser(_parser)
    assert len(wrapped(_trade(when), when)) == 1
    ticks[0] = 104.0
    assert len(wrapped(_trade(when), when)) == 1
    report = observer.snapshot()
    assert report["collecting"] is False
    assert report["wire_messages_by_topic_class"]["trade"] == 1
    assert report["parsed_events_by_class"]["trade"] == 1
    assert MAX_DURATION_SECONDS == 120


def test_b1_ack_outcomes_and_wire_classes_ignore_untrusted_details() -> None:
    when = datetime(2026, 10, 13, 11, tzinfo=UTC)
    secret = "private-token-not-to-log-291"
    observer = StageB1BulkObserver(duration_seconds=30)
    parser = observer.wrap_parser(_parser)
    assert parser({"op": "subscribe", "success": True, "ret_msg": secret}, when) == []
    assert parser({"op": "subscribe", "success": False, "ret_msg": secret}, when) == []
    assert parser({"op": "subscribe", "success": "true", "ret_msg": secret}, when) == []
    assert parser(_trade(when, price=secret), when)
    assert parser(_book(when), when)
    assert parser({"topic": secret, "payload": secret}, when) == []
    counts = observer.snapshot()
    assert counts["subscribe_ack_messages_by_outcome"] == {
        "success": 1,
        "rejected": 1,
        "unclassified": 1,
    }
    assert counts["wire_messages_by_topic_class"]["trade"] == 1
    assert counts["wire_messages_by_topic_class"]["orderbook"] == 1
    assert counts["wire_other_messages"] == 1
    assert counts["wire_control_messages"] == 3
    assert secret not in json.dumps(counts)
    assert not counts["enabled_in_production"]


def test_b1_parser_error_is_not_swallowed_and_timestamps_counted() -> None:
    when = datetime(2026, 10, 13, 11, tzinfo=UTC)
    observer = StageB1BulkObserver(duration_seconds=30)
    parser = observer.wrap_parser(_parser)
    trade = _trade(when)
    trade.pop("ts")
    assert parser(trade, when)[0].source_timestamp is None
    broken = _trade(when)
    broken["ts"] = "not-timestamp"
    with pytest.raises(ValueError):
        parser(broken, when)
    stats = observer.snapshot()
    assert stats["parsed_null_source_timestamp_by_class"]["trade"] == 1
    assert stats["parser_errors"] == 1
    assert stats["wire_messages_by_topic_class"]["trade"] == 2
    assert stats["parsed_events_by_class"]["trade"] == 1


@pytest.mark.asyncio
async def test_b1_enqueue_counts_only_after_routed_sink_returns() -> None:
    when = datetime(2026, 10, 13, 11, tzinfo=UTC)
    observer = StageB1BulkObserver(duration_seconds=30)
    bulk = EventBuffer(maxsize=1)
    priority = EventBuffer(maxsize=1)
    route = _RoutedBufferedEventSink(
        priority_sink=_BufferedEventSink(priority, lambda _: None),
        bulk_sink=_BufferedEventSink(bulk, lambda _: None),
        incident_sink=lambda _: None,
    )
    sink = observer.wrap_event_sink(route)
    parser = observer.wrap_parser(_parser)
    first = parser(_trade(when), when)[0]
    second = parser(_book(when), when)[0]

    await sink(first)
    blocked = asyncio.create_task(sink(second))
    for _ in range(20):
        await asyncio.sleep(0)
        if not blocked.done():
            break
    assert not blocked.done()
    assert observer.snapshot()["successfully_enqueued_events_by_class"] == {
        "trade": 1, "orderbook": 0, "liquidation": 0, "other": 0
    }
    assert (await bulk.get()) == first
    await asyncio.wait_for(blocked, timeout=1)
    assert (await bulk.get()) == second
    assert priority.empty()
    assert observer.snapshot()["successfully_enqueued_events_by_class"] == {
        "trade": 1, "orderbook": 1, "liquidation": 0, "other": 0
    }


@pytest.mark.asyncio
async def test_b1_database_writer_counts_postcommit_attempts_not_dedupe_inserts() -> None:
    when = datetime(2026, 10, 13, 11, tzinfo=UTC)
    observer = StageB1BulkObserver(duration_seconds=30)
    event = observer.wrap_parser(_parser)(_trade(when), when)[0]
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    repository = RecorderRepository()
    bulk = EventBuffer(maxsize=2)
    written_rows: list[int] = []

    async def transactional_sink(events: list[RawEvent]) -> None:
        with engine.begin() as connection:
            written_rows.append(repository.insert_events(connection, events))

    writer = BatchWriter(
        buffer=bulk,
        sink=observer.wrap_writer_sink(transactional_sink),
        batch_size=1,
        flush_interval_seconds=1,
    )
    try:
        await bulk.put(event)
        await bulk.put(event)
        stop = asyncio.Event()
        stop.set()
        await writer.run(stop)

        with engine.connect() as connection:
            rows = connection.execute(select(raw_market_events)).all()
        report = observer.snapshot()
        assert written_rows == [1, 0]  # second event deduplicated
        assert len(rows) == 1
        assert report["writer_attempted_events_by_class"]["trade"] == 2
        assert report[
            "events_in_successfully_returned_transactions_by_class"
        ]["trade"] == 2
        assert report["writer_batches_started"] == 2
        assert report["writer_batches_returned"] == 2
        assert report["writer_batches_failed"] == 0
        assert report["writer_batches_in_flight"] == 0
        assert "inserted_rows_by_topic" not in report
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_b1_failure_and_cancellation_are_not_misreported_as_commits() -> None:
    when = datetime(2026, 10, 13, 11, tzinfo=UTC)
    observer = StageB1BulkObserver(duration_seconds=30)
    event = observer.wrap_parser(_parser)(_trade(when), when)[0]

    async def fail_transaction(_: list[RawEvent]) -> None:
        raise RuntimeError("synthetic insert/commit failure")

    with pytest.raises(RuntimeError, match="synthetic insert/commit failure"):
        await observer.wrap_writer_sink(fail_transaction)([event])
    report = observer.snapshot()
    assert report["writer_batches_failed"] == 1
    assert report["writer_batches_returned"] == 0
    assert report[
        "events_in_successfully_returned_transactions_by_class"
    ]["trade"] == 0
    assert report["writer_batches_in_flight"] == 0

    async def cancelled(_: list[RawEvent]) -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await observer.wrap_writer_sink(cancelled)([event])
    assert observer.snapshot()["writer_batches_failed"] == 2
    assert observer.snapshot()["writer_batches_in_flight"] == 0


class _FakeSocket:
    def __init__(self, messages: list[str]) -> None:
        self.messages: asyncio.Queue[str] = asyncio.Queue()
        for message in messages:
            self.messages.put_nowait(message)
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        return await self.messages.get()


class _SocketConnection:
    def __init__(self, socket: _FakeSocket) -> None:
        self.socket = socket

    async def __aenter__(self) -> _FakeSocket:
        return self.socket

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


@pytest.mark.asyncio
async def test_b1_observer_sees_same_socket_bulk_messages_not_ticker() -> None:
    when = datetime(2026, 10, 13, 11, tzinfo=UTC)
    observer = StageB1BulkObserver(duration_seconds=30)
    socket = _FakeSocket(
        [
            json.dumps({"op": "subscribe", "success": True}),
            json.dumps(_book(when)),
            json.dumps(_trade(when)),
        ]
    )
    events: list[RawEvent] = []
    stop = asyncio.Event()

    async def sink(event: RawEvent) -> None:
        events.append(event)
        if len(events) == 2:
            stop.set()

    runner = WebSocketCollectorRunner(
        source="bybit",
        stream="linear",
        url="wss://example.test/linear",
        connector=lambda _: _SocketConnection(socket),
        subscription={
            "op": "subscribe",
            "args": [
                "orderbook.50.BTCUSDT",
                "publicTrade.BTCUSDT",
                "allLiquidation.BTCUSDT",
            ],
        },
        parser=observer.wrap_parser(_parser),
        event_sink=observer.wrap_event_sink(sink),
        incident_sink=lambda _: None,
        heartbeat_message=None,
        heartbeat_interval_seconds=None,
        now=lambda: when,
        component_name="bybit_linear",
    )
    await asyncio.wait_for(runner.run(stop), timeout=2)

    report = observer.snapshot()
    assert [event.event_type for event in events] == ["orderbook_delta", "trade"]
    assert report["wire_messages_by_topic_class"]["orderbook"] == 1
    assert report["wire_messages_by_topic_class"]["trade"] == 1
    assert report["subscribe_ack_messages_by_outcome"]["success"] == 1
    assert report["successfully_enqueued_events_by_class"]["trade"] == 1
    assert report["successfully_enqueued_events_by_class"]["orderbook"] == 1
    assert all("tickers.BTCUSDT" not in data for data in socket.sent)
