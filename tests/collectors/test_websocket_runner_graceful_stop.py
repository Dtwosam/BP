import asyncio
from datetime import datetime

import pytest

from bp_engine.collectors.websocket_runner import WebSocketCollectorRunner
from bp_engine.recorder.models import FeedIncident, RawEvent


class ReadyMessageWebSocket:
    def __init__(self) -> None:
        self.messages: asyncio.Queue[object] = asyncio.Queue()
        self.messages.put_nowait({"sequence": 1})
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> object:
        return await self.messages.get()


class FailingReceiveWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> object:
        raise RuntimeError("connection ended")


class FakeConnection:
    def __init__(self, websocket: object) -> None:
        self.websocket = websocket

    async def __aenter__(self) -> object:
        return self.websocket

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


def parse_trade(message: object, received_at: datetime) -> list[RawEvent]:
    return [
        RawEvent.build(
            source="test",
            stream="market",
            instrument="BTC",
            event_type="trade",
            source_timestamp=received_at,
            received_at=received_at,
            sequence=1,
            payload={"sequence": 1},
        )
    ]


async def stop_during_runner_wait(
    monkeypatch: pytest.MonkeyPatch,
    stop: asyncio.Event,
) -> None:
    original_wait = asyncio.wait

    async def simultaneous_stop(fs, *, return_when):
        stop.set()
        await asyncio.sleep(0)
        return await original_wait(fs, return_when=return_when)

    monkeypatch.setattr(asyncio, "wait", simultaneous_stop)


@pytest.mark.asyncio
async def test_graceful_stop_preserves_market_frame_already_ready_in_same_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop = asyncio.Event()
    ws = ReadyMessageWebSocket()
    incidents: list[FeedIncident] = []
    observed_events: list[RawEvent] = []
    await stop_during_runner_wait(monkeypatch, stop)

    runner = WebSocketCollectorRunner(
        source="test",
        stream="market",
        url="wss://example.test/ws",
        connector=lambda url: FakeConnection(ws),
        subscription={"type": "market"},
        parser=parse_trade,
        event_sink=observed_events.append,
        incident_sink=incidents.append,
        heartbeat_message=None,
        heartbeat_interval_seconds=None,
    )

    await asyncio.wait_for(runner.run(stop), timeout=1)

    assert len(observed_events) == 1
    assert [incident.incident_type for incident in incidents] == [
        "connected",
        "disconnected",
    ]


@pytest.mark.asyncio
async def test_graceful_stop_still_suppresses_same_turn_receive_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop = asyncio.Event()
    ws = FailingReceiveWebSocket()
    incidents: list[FeedIncident] = []
    await stop_during_runner_wait(monkeypatch, stop)

    runner = WebSocketCollectorRunner(
        source="test",
        stream="market",
        url="wss://example.test/ws",
        connector=lambda url: FakeConnection(ws),
        subscription={"type": "market"},
        parser=parse_trade,
        event_sink=lambda event: None,
        incident_sink=incidents.append,
        heartbeat_message=None,
        heartbeat_interval_seconds=None,
    )

    await asyncio.wait_for(runner.run(stop), timeout=1)

    assert [incident.incident_type for incident in incidents] == [
        "connected",
        "disconnected",
    ]
