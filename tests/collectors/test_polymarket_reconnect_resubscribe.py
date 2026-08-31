import asyncio
from datetime import datetime

import pytest

from bp_engine.collectors.reliability import ReconnectPolicy
from bp_engine.collectors.websocket_runner import WebSocketCollectorRunner


class FakeWebSocket:
    def __init__(self, messages: list[object] | None = None) -> None:
        self.messages: asyncio.Queue[object] = asyncio.Queue()
        for message in messages or []:
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
        if not self.websockets:
            raise RuntimeError("no fake websocket available")
        return FakeConnection(self.websockets.pop(0))


@pytest.mark.asyncio
async def test_polymarket_dynamic_asset_set_is_restored_after_reconnect() -> None:
    first = FakeWebSocket()
    second = FakeWebSocket(["{\"event_type\":\"noop\"}"])
    connector = FakeConnector([first, second])
    stop = asyncio.Event()
    outbound: asyncio.Queue[object] = asyncio.Queue()

    def parser(message: object, received_at: datetime) -> list[object]:
        if message == {"event_type": "noop"}:
            stop.set()
        return []

    runner = WebSocketCollectorRunner(
        source="polymarket",
        stream="market",
        url="wss://example.test/ws",
        connector=connector,
        subscription={"assets_ids": ["old"], "type": "market"},
        parser=parser,
        event_sink=lambda event: None,
        incident_sink=lambda incident: None,
        heartbeat_message="PING",
        heartbeat_interval_seconds=30,
        reconnect_policy=ReconnectPolicy(initial_seconds=0, maximum_seconds=0),
        outbound_messages=outbound,
    )

    task = asyncio.create_task(runner.run(stop))
    for _ in range(100):
        if first.sent:
            break
        await asyncio.sleep(0.002)

    await outbound.put({"operation": "subscribe", "assets_ids": ["new"]})
    for _ in range(100):
        if '{"operation":"subscribe","assets_ids":["new"]}' in first.sent:
            break
        await asyncio.sleep(0.002)

    await outbound.put({"operation": "unsubscribe", "assets_ids": ["old"]})
    for _ in range(100):
        if '{"operation":"unsubscribe","assets_ids":["old"]}' in first.sent:
            break
        await asyncio.sleep(0.002)

    await first.messages.put(RuntimeError("socket dropped"))
    await asyncio.wait_for(task, timeout=1)

    assert second.sent[0] == '{"assets_ids":["new"],"type":"market"}'
