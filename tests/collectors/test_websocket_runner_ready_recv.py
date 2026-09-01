import asyncio
from datetime import datetime

import pytest

from bp_engine.collectors.websocket_runner import WebSocketCollectorRunner
from bp_engine.recorder.models import FeedIncident, RawEvent


class FailingControlSendWebSocket:
    def __init__(self, stop: asyncio.Event) -> None:
        self.stop = stop
        self.messages: asyncio.Queue[object] = asyncio.Queue()
        self.messages.put_nowait({"sequence": 1})
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)
        if len(self.sent) == 2:
            self.stop.set()
            raise RuntimeError("control send failed")

    async def recv(self) -> object:
        return await self.messages.get()


class FakeConnection:
    def __init__(self, websocket: FailingControlSendWebSocket) -> None:
        self.websocket = websocket

    async def __aenter__(self) -> FailingControlSendWebSocket:
        return self.websocket

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


@pytest.mark.asyncio
async def test_ready_market_data_is_processed_before_same_turn_outbound_send_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop = asyncio.Event()
    ws = FailingControlSendWebSocket(stop)
    outbound: asyncio.Queue[object] = asyncio.Queue()
    outbound.put_nowait({"operation": "subscribe", "assets_ids": ["next"]})
    incidents: list[FeedIncident] = []
    observed_events: list[RawEvent] = []
    original_wait = asyncio.wait

    async def delayed_wait(fs, *, return_when):
        # Let both the ready receive and ready outbound-control task complete
        # before the runner observes the done set.
        await asyncio.sleep(0.003)
        return await original_wait(fs, return_when=return_when)

    monkeypatch.setattr(asyncio, "wait", delayed_wait)

    def parser(message: object, received_at: datetime) -> list[RawEvent]:
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
        subscription={"type": "market", "assets_ids": ["current"]},
        parser=parser,
        event_sink=observed_events.append,
        incident_sink=incidents.append,
        heartbeat_message=None,
        heartbeat_interval_seconds=None,
        outbound_messages=outbound,
    )

    await asyncio.wait_for(runner.run(stop), timeout=1)

    assert len(observed_events) == 1
    assert [incident.incident_type for incident in incidents] == [
        "connected",
        "error",
        "disconnected",
    ]
