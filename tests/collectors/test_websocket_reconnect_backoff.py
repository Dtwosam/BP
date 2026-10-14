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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("first_healthy_market", "expected_attempts", "expected_events"),
    [
        (False, [0, 1], ["10"]),
        (True, [0, 0], ["9", "10"]),
    ],
)
async def test_control_only_receive_does_not_reset_consecutive_failure_streak(
    first_healthy_market: bool,
    expected_attempts: list[int],
    expected_events: list[str],
) -> None:
    """PONG/ACK traffic is not successful market-data recovery."""
    first = FakeWebSocket([RuntimeError("initial socket failure")])
    middle_messages: list[object] = ['{"type": "PONG"}']
    if first_healthy_market:
        middle_messages.append('{"sequence": 9}')
    middle_messages.append(RuntimeError("control-only or market-data socket drop"))
    second = FakeWebSocket(middle_messages)
    third = FakeWebSocket(['{"sequence": 10}'])
    connector = FakeConnector([first, second, third])
    incidents: list[FeedIncident] = []
    events: list[RawEvent] = []
    stop = asyncio.Event()

    def parser(message: object, received_at: datetime) -> list[RawEvent]:
        assert isinstance(message, dict)
        if message.get("type") == "PONG":
            return []
        sequence = int(message["sequence"])
        if sequence == 10:
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
        heartbeat_message=None,
        heartbeat_interval_seconds=None,
        reconnect_policy=ReconnectPolicy(initial_seconds=0, maximum_seconds=0),
    )

    await asyncio.wait_for(runner.run(stop), timeout=1)

    reconnect_attempts = [
        incident.details["attempt"]
        for incident in incidents
        if incident.incident_type == "reconnect"
    ]
    assert reconnect_attempts == expected_attempts
    assert [event.sequence for event in events] == expected_events
    assert [incident.incident_type for incident in incidents].count("error") == 2


@pytest.mark.asyncio
async def test_runner_keeps_reconnecting_after_1024_consecutive_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real runner must not die while calculating capped backoff."""
    failure_count = 1025
    sockets = [
        FakeWebSocket([RuntimeError("fixed synthetic disconnect")])
        for _ in range(failure_count)
    ]
    sockets.append(FakeWebSocket(['{"sequence": 999}']))
    connector = FakeConnector(sockets)
    incidents: list[FeedIncident] = []
    delivered: list[RawEvent] = []
    scheduled_delays: list[float] = []
    stop = asyncio.Event()

    def parser(message: object, received_at: datetime) -> list[RawEvent]:
        assert isinstance(message, dict)
        stop.set()
        return [raw_event(int(message["sequence"]), received_at)]

    runner = WebSocketCollectorRunner(
        source="test",
        stream="market",
        url="wss://fixed-fake.test/ws",
        connector=connector,
        subscription={"type": "market"},
        parser=parser,
        event_sink=delivered.append,
        incident_sink=incidents.append,
        heartbeat_message=None,
        heartbeat_interval_seconds=None,
        reconnect_policy=ReconnectPolicy(),
    )

    async def fake_no_wait(_stop: asyncio.Event, delay: float) -> None:
        scheduled_delays.append(delay)
        await asyncio.sleep(0)

    # Exercise the real runner/error loop and real default delay policy;
    # only the wait duration is replaced to avoid a 30-second test sleep.
    monkeypatch.setattr(runner, "_wait_before_reconnect", fake_no_wait)
    await asyncio.wait_for(runner.run(stop), timeout=10)

    attempts = [
        incident.details["attempt"]
        for incident in incidents
        if incident.incident_type == "reconnect"
    ]
    assert len(attempts) == failure_count
    assert attempts[:6] == list(range(6))
    assert attempts[-1] == 1024
    assert scheduled_delays[:7] == [1, 2, 4, 8, 16, 30, 30]
    assert scheduled_delays[-1] == 30
    assert len(delivered) == 1
    assert delivered[0].sequence == "999"
    assert not connector.websockets
