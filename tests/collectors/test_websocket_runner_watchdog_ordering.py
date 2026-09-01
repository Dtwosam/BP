import asyncio
from datetime import datetime

import pytest

from bp_engine.collectors.reliability import FeedWatchdog
from bp_engine.collectors.websocket_runner import WebSocketCollectorRunner
from bp_engine.recorder.models import FeedIncident, RawEvent


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages: asyncio.Queue[object] = asyncio.Queue()
        self.messages.put_nowait({"sequence": 1})
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


@pytest.mark.asyncio
async def test_ready_market_data_is_processed_before_same_turn_stale_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = FakeWebSocket()
    stop = asyncio.Event()
    incidents: list[FeedIncident] = []
    original_wait = asyncio.wait

    async def delayed_wait(fs, *, return_when):
        # Let both the already-ready recv task and the watchdog timer become
        # complete before the runner receives the done set. This isolates the
        # ordering contract inside one runner loop turn.
        await asyncio.sleep(0.003)
        return await original_wait(fs, return_when=return_when)

    monkeypatch.setattr(asyncio, "wait", delayed_wait)

    def parser(message: object, received_at: datetime) -> list[RawEvent]:
        stop.set()
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

    runner = WebSocketCollectorRunner(
        source="test",
        stream="market",
        url="wss://example.test/ws",
        connector=lambda url: FakeConnection(ws),
        subscription={"type": "market"},
        parser=parser,
        event_sink=lambda event: None,
        incident_sink=incidents.append,
        heartbeat_message=None,
        heartbeat_interval_seconds=None,
        watchdog=FeedWatchdog(stale_after_seconds=0.0001),
    )

    await asyncio.wait_for(runner.run(stop), timeout=1)

    incident_types = [incident.incident_type for incident in incidents]
    assert "stale" not in incident_types
    assert "recovered" not in incident_types
