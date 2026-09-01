import asyncio
import json
from datetime import datetime

import pytest

from bp_engine.collectors.reliability import FeedWatchdog, ReconnectPolicy
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
        self.opened: list[FakeWebSocket] = []

    def __call__(self, url: str) -> FakeConnection:
        if not self.websockets:
            raise RuntimeError("no fake websocket available")
        websocket = self.websockets.pop(0)
        self.opened.append(websocket)
        return FakeConnection(websocket)


def extract_asset_ids(message: object) -> frozenset[str]:
    if not isinstance(message, dict):
        return frozenset()
    asset_id = message.get("asset_id")
    if not asset_id:
        return frozenset()
    return frozenset({str(asset_id)})


@pytest.mark.asyncio
async def test_missing_rotated_asset_forces_reconnect_while_stream_stays_healthy() -> None:
    first = FakeWebSocket()
    second = FakeWebSocket(
        [json.dumps({"event_type": "stop", "asset_id": "fifteen-minute"})]
    )
    connector = FakeConnector([first, second])
    stop = asyncio.Event()
    outbound: asyncio.Queue[object] = asyncio.Queue()
    incidents: list[object] = []

    def parser(message: object, received_at: datetime) -> list[object]:
        if isinstance(message, dict) and message.get("event_type") == "stop":
            stop.set()
        return []

    runner = WebSocketCollectorRunner(
        source="polymarket",
        stream="market",
        url="wss://example.test/ws",
        connector=connector,
        subscription={"assets_ids": ["five-minute"], "type": "market"},
        parser=parser,
        event_sink=lambda event: None,
        incident_sink=incidents.append,
        heartbeat_message="PING",
        heartbeat_interval_seconds=30,
        reconnect_policy=ReconnectPolicy(initial_seconds=0, maximum_seconds=0),
        outbound_messages=outbound,
        watchdog=FeedWatchdog(stale_after_seconds=0.1),
        subscription_liveness_stale_after_seconds=0.04,
        subscription_asset_id_extractor=extract_asset_ids,
    )

    task = asyncio.create_task(runner.run(stop))
    for _ in range(100):
        if first.sent:
            break
        await asyncio.sleep(0.002)

    await outbound.put(
        {"operation": "subscribe", "assets_ids": ["fifteen-minute"]}
    )
    for _ in range(100):
        if (
            '{"operation":"subscribe","assets_ids":["fifteen-minute"]}'
            in first.sent
        ):
            break
        await asyncio.sleep(0.002)

    async def keep_five_minute_asset_alive() -> None:
        while len(connector.opened) < 2 and not stop.is_set():
            await first.messages.put(
                json.dumps({"event_type": "book", "asset_id": "five-minute"})
            )
            await asyncio.sleep(0.005)

    producer = asyncio.create_task(keep_five_minute_asset_alive())
    try:
        await asyncio.wait_for(task, timeout=1)
    finally:
        producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)

    assert len(connector.opened) == 2
    assert second.sent[0] == (
        '{"assets_ids":["fifteen-minute","five-minute"],"type":"market"}'
    )

    stale_incidents = [
        incident
        for incident in incidents
        if getattr(incident, "incident_type", None) == "subscription_stale"
    ]
    assert len(stale_incidents) == 1
    assert stale_incidents[0].details["asset_ids"] == ["fifteen-minute"]
    assert not any(
        getattr(incident, "incident_type", None) == "stale" for incident in incidents
    )
