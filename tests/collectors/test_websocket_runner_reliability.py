import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from bp_engine.collectors.reliability import ClockSkewGuard, FeedWatchdog
from bp_engine.collectors.websocket_runner import WebSocketCollectorRunner
from bp_engine.recorder.models import FeedIncident, RawEvent


class FakeWebSocket:
    def __init__(self, messages: list[object] | None = None) -> None:
        self.messages: asyncio.Queue[object] = asyncio.Queue()
        for message in messages or []:
            self.messages.put_nowait(message)
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> object:
        return await self.messages.get()


class FakeConnection:
    def __init__(self, websocket: FakeWebSocket) -> None:
        self.websocket = websocket

    async def __aenter__(self) -> FakeWebSocket:
        return self.websocket

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class FakeConnector:
    def __init__(self, websocket: FakeWebSocket) -> None:
        self.websocket = websocket

    def __call__(self, url: str) -> FakeConnection:
        return FakeConnection(self.websocket)


@pytest.mark.asyncio
async def test_runner_emits_stale_incident_when_connected_feed_goes_quiet() -> None:
    ws = FakeWebSocket()
    stop = asyncio.Event()
    incidents: list[FeedIncident] = []

    def incident_sink(incident: FeedIncident) -> None:
        incidents.append(incident)
        if incident.incident_type == "stale":
            stop.set()

    runner = WebSocketCollectorRunner(
        source="coinbase",
        stream="spot",
        url="wss://example.test/ws",
        connector=FakeConnector(ws),
        subscription={"type": "subscribe", "channel": "heartbeats"},
        parser=lambda message, received_at: [],
        event_sink=lambda event: None,
        incident_sink=incident_sink,
        heartbeat_message=None,
        heartbeat_interval_seconds=None,
        watchdog=FeedWatchdog(stale_after_seconds=0.01),
    )

    await asyncio.wait_for(runner.run(stop), timeout=1)

    assert "stale" in [incident.incident_type for incident in incidents]


@pytest.mark.asyncio
async def test_runner_emits_recovered_when_reconnect_clears_stale_watchdog() -> None:
    watchdog = FeedWatchdog(stale_after_seconds=1)
    observed_at = datetime(2026, 8, 23, tzinfo=UTC)
    watchdog.observe(
        "bybit",
        "spot",
        monotonic_time=0,
        observed_at=observed_at,
    )
    stale = watchdog.check(
        "bybit",
        "spot",
        monotonic_time=2,
        observed_at=observed_at + timedelta(seconds=2),
    )
    assert stale is not None
    assert stale.incident_type == "stale"

    ws = FakeWebSocket(['{"type":"snapshot"}'])
    stop = asyncio.Event()
    incidents: list[FeedIncident] = []

    def parser(message: object, received_at: datetime) -> list[RawEvent]:
        stop.set()
        return []

    runner = WebSocketCollectorRunner(
        source="bybit",
        stream="spot",
        url="wss://example.test/ws",
        connector=FakeConnector(ws),
        subscription={"op": "subscribe", "args": ["orderbook.1.BTCUSDT"]},
        parser=parser,
        event_sink=lambda event: None,
        incident_sink=incidents.append,
        heartbeat_message=None,
        heartbeat_interval_seconds=None,
        watchdog=watchdog,
    )

    await asyncio.wait_for(runner.run(stop), timeout=1)

    assert "recovered" in [incident.incident_type for incident in incidents]


@pytest.mark.asyncio
async def test_runner_emits_clock_skew_incident_for_future_source_time() -> None:
    ws = FakeWebSocket(['{"sequence": 9}'])
    stop = asyncio.Event()
    incidents: list[FeedIncident] = []

    def parser(message: object, received_at: datetime) -> list[RawEvent]:
        stop.set()
        return [
            RawEvent.build(
                source="test",
                stream="market",
                instrument="BTC",
                event_type="trade",
                source_timestamp=received_at + timedelta(seconds=10),
                received_at=received_at,
                sequence=9,
                payload={"sequence": 9},
            )
        ]

    runner = WebSocketCollectorRunner(
        source="test",
        stream="market",
        url="wss://example.test/ws",
        connector=FakeConnector(ws),
        subscription={"type": "market"},
        parser=parser,
        event_sink=lambda event: None,
        incident_sink=incidents.append,
        heartbeat_message="PING",
        heartbeat_interval_seconds=30,
        clock_skew_guard=ClockSkewGuard(max_abs_skew_seconds=5),
    )

    await asyncio.wait_for(runner.run(stop), timeout=1)

    assert "clock_skew" in [incident.incident_type for incident in incidents]
