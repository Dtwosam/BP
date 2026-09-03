import asyncio
import json
from dataclasses import dataclass
from datetime import datetime

import pytest

from bp_engine.collectors.reliability import ReconnectPolicy
from bp_engine.collectors.websocket_runner import WebSocketCollectorRunner
from bp_engine.recorder.models import FeedIncident, RawEvent
from bp_engine.recorder.service import _BufferedEventSink
from bp_engine.recorder.writer import BatchWriter, EventBuffer

BURST_EVENTS = 150
BURST_INTERVAL_SECONDS = 0.001
DATABASE_BATCH_LATENCY_SECONDS = 0.002
BUFFER_MAXSIZE = 8
SERVER_BACKLOG_LIMIT = 12


class BurstWebSocket:
    def __init__(self, *, max_backlog: int) -> None:
        self.messages: asyncio.Queue[object] = asyncio.Queue()
        self.sent: list[str] = []
        self.max_backlog = max_backlog

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> object:
        if self.messages.qsize() > self.max_backlog:
            raise RuntimeError("slow consumer: synthetic send buffer full")
        return await self.messages.get()

    async def produce(self, stop: asyncio.Event) -> None:
        for sequence in range(BURST_EVENTS):
            if stop.is_set():
                return
            await self.messages.put(json.dumps({"sequence": sequence}))
            await asyncio.sleep(BURST_INTERVAL_SECONDS)


class BurstConnection:
    def __init__(self, websocket: BurstWebSocket) -> None:
        self.websocket = websocket

    async def __aenter__(self) -> BurstWebSocket:
        return self.websocket

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class BurstConnector:
    def __init__(self, websocket: BurstWebSocket) -> None:
        self.websocket = websocket
        self.calls = 0

    def __call__(self, url: str) -> BurstConnection:
        self.calls += 1
        return BurstConnection(self.websocket)


@dataclass(frozen=True)
class BurstResult:
    stored_sequences: tuple[str, ...]
    incidents: tuple[FeedIncident, ...]
    sent_messages: tuple[str, ...]
    connector_calls: int


def parse_burst_message(message: object, received_at: datetime) -> list[RawEvent]:
    assert isinstance(message, dict)
    sequence = int(message["sequence"])
    return [
        RawEvent.build(
            source="polymarket",
            stream="market",
            instrument="condition-stress",
            event_type="last_trade_price",
            source_timestamp=received_at,
            received_at=received_at,
            sequence=sequence,
            asset_id="token-stress",
            payload={"sequence": sequence},
        )
    ]


async def exercise_burst(*, worker_count: int) -> BurstResult:
    stop = asyncio.Event()
    stored_complete = asyncio.Event()
    error_seen = asyncio.Event()
    incidents: list[FeedIncident] = []
    stored_sequences: list[str] = []
    buffer = EventBuffer(maxsize=BUFFER_MAXSIZE)

    def record_incident(incident: FeedIncident) -> None:
        incidents.append(incident)
        if incident.incident_type == "error":
            error_seen.set()
            stop.set()

    event_sink = _BufferedEventSink(buffer, record_incident)

    async def database_sink(items: list[RawEvent]) -> None:
        await asyncio.sleep(DATABASE_BATCH_LATENCY_SECONDS)
        stored_sequences.extend(str(item.sequence) for item in items)
        if len(stored_sequences) == BURST_EVENTS:
            stored_complete.set()

    writer = BatchWriter(
        buffer=buffer,
        sink=database_sink,
        batch_size=1,
        flush_interval_seconds=0.01,
        worker_count=worker_count,
    )
    websocket = BurstWebSocket(max_backlog=SERVER_BACKLOG_LIMIT)
    connector = BurstConnector(websocket)
    outbound: asyncio.Queue[object] = asyncio.Queue()
    runner = WebSocketCollectorRunner(
        source="polymarket",
        stream="market",
        url="wss://example.test/market",
        connector=connector,
        subscription={"assets_ids": ["token-stress"], "type": "market"},
        parser=parse_burst_message,
        event_sink=event_sink,
        incident_sink=record_incident,
        heartbeat_message="PING",
        heartbeat_interval_seconds=0.005,
        reconnect_policy=ReconnectPolicy(initial_seconds=0, maximum_seconds=0),
        outbound_messages=outbound,
    )

    async def send_control_update() -> None:
        await asyncio.sleep(0.02)
        if not stop.is_set():
            await outbound.put({"operation": "subscribe", "assets_ids": ["token-extra"]})

    writer_task = asyncio.create_task(writer.run(stop))
    runner_task = asyncio.create_task(runner.run(stop))
    producer_task = asyncio.create_task(websocket.produce(stop))
    control_task = asyncio.create_task(send_control_update())
    stored_wait = asyncio.create_task(stored_complete.wait())
    error_wait = asyncio.create_task(error_seen.wait())

    try:
        done, _ = await asyncio.wait(
            {stored_wait, error_wait},
            timeout=2,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            raise AssertionError("synthetic burst neither completed nor failed closed")
    finally:
        stop.set()
        for task in (stored_wait, error_wait):
            if not task.done():
                task.cancel()
        await asyncio.gather(stored_wait, error_wait, return_exceptions=True)
        await asyncio.gather(
            producer_task,
            control_task,
            runner_task,
            writer_task,
            return_exceptions=False,
        )

    return BurstResult(
        stored_sequences=tuple(stored_sequences),
        incidents=tuple(incidents),
        sent_messages=tuple(websocket.sent),
        connector_calls=connector.calls,
    )


@pytest.mark.asyncio
async def test_single_writer_fixture_reproduces_local_slow_consumer_failure() -> None:
    result = await exercise_burst(worker_count=1)

    assert len(result.stored_sequences) < BURST_EVENTS
    assert any(
        incident.incident_type == "error"
        and "slow consumer" in str(incident.details.get("message", ""))
        for incident in result.incidents
    )


@pytest.mark.asyncio
async def test_bounded_writer_pool_sustains_polymarket_burst_without_starving_control() -> None:
    result = await exercise_burst(worker_count=4)

    assert len(result.stored_sequences) == BURST_EVENTS
    assert set(result.stored_sequences) == {str(sequence) for sequence in range(BURST_EVENTS)}
    assert result.connector_calls == 1
    assert "PING" in result.sent_messages
    assert (
        '{"operation":"subscribe","assets_ids":["token-extra"]}' in result.sent_messages
    )
    assert not any(incident.incident_type == "error" for incident in result.incidents)
    assert not any(incident.incident_type == "reconnect" for incident in result.incidents)
    assert sum(incident.incident_type == "backpressure" for incident in result.incidents) <= 2


@pytest.mark.asyncio
async def test_unsustainable_overload_blocks_instead_of_silently_dropping() -> None:
    buffer = EventBuffer(maxsize=1)
    incidents: list[FeedIncident] = []
    event_sink = _BufferedEventSink(buffer, incidents.append)
    now = datetime.now().astimezone()

    def event(sequence: int) -> RawEvent:
        return RawEvent.build(
            source="polymarket",
            stream="market",
            instrument="condition-overload",
            event_type="last_trade_price",
            source_timestamp=now,
            received_at=now,
            sequence=sequence,
            asset_id="token-overload",
            payload={"sequence": sequence},
        )

    buffer.put_nowait(event(0))
    first = asyncio.create_task(event_sink(event(1)))
    second = asyncio.create_task(event_sink(event(2)))
    await asyncio.sleep(0.01)

    assert not first.done()
    assert not second.done()
    assert buffer.qsize() == 1
    assert [incident.incident_type for incident in incidents] == ["backpressure"]

    drained = [(await buffer.get()).sequence]
    await asyncio.sleep(0)
    drained.append((await buffer.get()).sequence)
    await asyncio.gather(first, second)
    drained.append((await buffer.get()).sequence)

    assert set(drained) == {"0", "1", "2"}
