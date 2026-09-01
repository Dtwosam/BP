import asyncio
import json
from collections.abc import Sequence

import pytest

from bp_engine.collectors.reliability import ReconnectPolicy
from bp_engine.collectors.websocket_runner import WebSocketCollectorRunner
from bp_engine.recorder.models import FeedIncident


class FakeWebSocket:
    def __init__(
        self,
        *,
        messages: Sequence[object] = (),
        stop: asyncio.Event | None = None,
        stop_on_first_send: bool = False,
    ) -> None:
        self.messages: asyncio.Queue[object] = asyncio.Queue()
        for message in messages:
            self.messages.put_nowait(message)
        self.stop = stop
        self.stop_on_first_send = stop_on_first_send
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)
        if self.stop_on_first_send and len(self.sent) == 1:
            assert self.stop is not None
            self.stop.set()

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


class SequenceConnector:
    def __init__(self, websockets: list[FakeWebSocket]) -> None:
        self.websockets = websockets

    def __call__(self, url: str) -> FakeConnection:
        del url
        if not self.websockets:
            raise RuntimeError("no fake websocket available")
        return FakeConnection(self.websockets.pop(0))


@pytest.mark.asyncio
async def test_ready_outbound_subscription_is_retained_when_receive_fails_same_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop = asyncio.Event()
    first = FakeWebSocket(messages=[RuntimeError("connection ended")])
    second = FakeWebSocket(stop=stop, stop_on_first_send=True)
    connector = SequenceConnector([first, second])
    outbound: asyncio.Queue[object] = asyncio.Queue()
    outbound.put_nowait({"operation": "subscribe", "assets_ids": ["next"]})
    incidents: list[FeedIncident] = []
    original_wait = asyncio.wait
    wait_calls = 0

    async def simultaneous_first_wait(fs, *, return_when):
        nonlocal wait_calls
        wait_calls += 1
        if wait_calls == 1:
            # The receive failure and Queue.get() must both be complete before
            # arbitration, reproducing the control-orphan race deterministically.
            await asyncio.sleep(0.003)
        return await original_wait(fs, return_when=return_when)

    monkeypatch.setattr(asyncio, "wait", simultaneous_first_wait)

    runner = WebSocketCollectorRunner(
        source="polymarket",
        stream="market",
        url="wss://example.test/ws",
        connector=connector,
        subscription={"type": "market", "assets_ids": ["current"]},
        parser=lambda message, received_at: [],
        event_sink=lambda event: None,
        incident_sink=incidents.append,
        heartbeat_message=None,
        heartbeat_interval_seconds=None,
        outbound_messages=outbound,
        reconnect_policy=ReconnectPolicy(initial_seconds=0, maximum_seconds=0),
    )

    await asyncio.wait_for(runner.run(stop), timeout=1)

    assert len(second.sent) == 1
    reconnect_subscription = json.loads(second.sent[0])
    assert reconnect_subscription["assets_ids"] == ["current", "next"]
