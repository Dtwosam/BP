import asyncio
from datetime import datetime

import pytest

from bp_engine.collectors.reliability import ReconnectPolicy
from bp_engine.collectors.websocket_runner import WebSocketCollectorRunner
from bp_engine.recorder.models import FeedIncident, RawEvent


class FakeWebSocket:
    def __init__(self, messages: list[object]) -> None:
        self.messages: asyncio.Queue[object] = asyncio.Queue()
        for message in messages:
            self.messages.put_nowait(message)
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> object:
        message = await self.messages.get()
        if isinstance(message, BaseException):
            raise message
        return message


class FakeConnection:
    def __init__(self, websocket: FakeWebSocket) -> None:
        self.websocket = websocket

    async def __aenter__(self) -> FakeWebSocket:
        return self.websocket

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class FakeConnector:
    def __init__(self, websockets: list[FakeWebSocket]) -> None:
        self.websockets = websockets

    def __call__(self, url: str) -> FakeConnection:
        del url
        if not self.websockets:
            raise RuntimeError("no fake websocket available")
        return FakeConnection(self.websockets.pop(0))


def raw_event(sequence: int, received_at: datetime) -> RawEvent:
    return RawEvent.build(
        source="test",
        stream="market",
        instrument="BTC",
        event_type="trade",
        source_timestamp=received_at,
        received_at=received_at,
        sequence=sequence,
        payload={"sequence": sequence},
    )


@pytest.mark.asyncio
async def test_reconnect_streak_resets_after_connection_receives_data() -> None:
    first = FakeWebSocket([RuntimeError("first drop")])
    second = FakeWebSocket(['{"sequence": 1}', RuntimeError("second drop")])
    third = FakeWebSocket(['{"sequence": 2}'])
    connector = FakeConnector([first, second, third])
    incidents: list[FeedIncident] = []
    events: list[RawEvent] = []
    stop = asyncio.Event()

    def parser(message: object, received_at: datetime) -> list[RawEvent]:
        assert isinstance(message, dict)
        sequence = int(message["sequence"])
        if sequence == 2:
            stop.set()
        return [raw_event(sequence, received_at)]

    runner = WebSocketCollectorRunner(
        source="test",
        stream="market",
        url="wss://example.test/ws",
        connector=connector,
        subscription={"type": "market"},
        parser=parser,
        event_sink=events.append,
        incident_sink=incidents.append,
        heartbeat_message="PING",
        heartbeat_interval_seconds=30,
        reconnect_policy=ReconnectPolicy(initial_seconds=0, maximum_seconds=0),
    )

    await asyncio.wait_for(runner.run(stop), timeout=1)

    reconnect_attempts = [
        incident.details["attempt"]
        for incident in incidents
        if incident.incident_type == "reconnect"
    ]
    assert reconnect_attempts == [0, 0]
    assert [event.sequence for event in events] == ["1", "2"]


@pytest.mark.asyncio
async def test_reconnect_streak_keeps_escalating_until_a_connection_receives_data() -> None:
    first = FakeWebSocket([RuntimeError("first drop")])
    second = FakeWebSocket([RuntimeError("second drop")])
    third = FakeWebSocket(['{"sequence": 3}'])
    connector = FakeConnector([first, second, third])
    incidents: list[FeedIncident] = []
    stop = asyncio.Event()

    def parser(message: object, received_at: datetime) -> list[RawEvent]:
        assert isinstance(message, dict)
        stop.set()
        return [raw_event(int(message["sequence"]), received_at)]

    runner = WebSocketCollectorRunner(
        source="test",
        stream="market",
        url="wss://example.test/ws",
        connector=connector,
        subscription={"type": "market"},
        parser=parser,
        event_sink=lambda event: None,
        incident_sink=incidents.append,
        heartbeat_message="PING",
        heartbeat_interval_seconds=30,
        reconnect_policy=ReconnectPolicy(initial_seconds=0, maximum_seconds=0),
    )

    await asyncio.wait_for(runner.run(stop), timeout=1)

    reconnect_attempts = [
        incident.details["attempt"]
        for incident in incidents
        if incident.incident_type == "reconnect"
    ]
    assert reconnect_attempts == [0, 1]
