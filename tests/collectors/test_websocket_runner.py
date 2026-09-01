import asyncio
from datetime import datetime

import pytest

from bp_engine.collectors.reliability import ReconnectPolicy
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
        self.urls: list[str] = []

    def __call__(self, url: str) -> FakeConnection:
        self.urls.append(url)
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
async def test_runner_subscribes_and_forwards_parsed_events() -> None:
    ws = FakeWebSocket(["{\"sequence\": 7}"])
    connector = FakeConnector([ws])
    events: list[RawEvent] = []
    incidents: list[FeedIncident] = []
    stop = asyncio.Event()

    def parser(message: object, received_at: datetime) -> list[RawEvent]:
        stop.set()
        assert message == {"sequence": 7}
        return [raw_event(7, received_at)]

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
    )

    await asyncio.wait_for(runner.run(stop), timeout=1)

    assert connector.urls == ["wss://example.test/ws"]
    assert ws.sent[0] == '{"type":"market"}'
    assert [event.sequence for event in events] == ["7"]
    assert incidents[0].incident_type == "connected"


@pytest.mark.asyncio
async def test_runner_sends_heartbeat_while_feed_is_quiet_and_stops_promptly() -> None:
    ws = FakeWebSocket()
    connector = FakeConnector([ws])
    stop = asyncio.Event()

    runner = WebSocketCollectorRunner(
        source="polymarket",
        stream="market",
        url="wss://example.test/ws",
        connector=connector,
        subscription={"type": "market"},
        parser=lambda message, received_at: [],
        event_sink=lambda event: None,
        incident_sink=lambda incident: None,
        heartbeat_message="PING",
        heartbeat_interval_seconds=0.01,
    )

    task = asyncio.create_task(runner.run(stop))
    for _ in range(100):
        if "PING" in ws.sent:
            break
        await asyncio.sleep(0.002)
    stop.set()
    await asyncio.wait_for(task, timeout=1)

    assert "PING" in ws.sent


@pytest.mark.asyncio
async def test_runner_reconnects_after_connection_error() -> None:
    first = FakeWebSocket([RuntimeError("socket dropped")])
    second = FakeWebSocket(["{\"sequence\": 8}"])
    connector = FakeConnector([first, second])
    events: list[RawEvent] = []
    incidents: list[FeedIncident] = []
    stop = asyncio.Event()

    def parser(message: object, received_at: datetime) -> list[RawEvent]:
        stop.set()
        return [raw_event(8, received_at)]

    runner = WebSocketCollectorRunner(
        source="test",
        stream="market",
        url="wss://example.test/ws",
        connector=connector,
        subscription={"type": "market"},
        parser=parser,
        event_sink=events.append,
        incident_sink=incidents.append,
        heartbeat_message={"op": "ping"},
        heartbeat_interval_seconds=30,
        reconnect_policy=ReconnectPolicy(initial_seconds=0, maximum_seconds=0),
    )

    await asyncio.wait_for(runner.run(stop), timeout=1)

    assert len(connector.urls) == 2
    assert [event.sequence for event in events] == ["8"]
    incident_types = [incident.incident_type for incident in incidents]
    assert "error" in incident_types
    assert "reconnect" in incident_types


@pytest.mark.asyncio
async def test_runner_serializes_mapping_heartbeat_compactly() -> None:
    ws = FakeWebSocket()
    connector = FakeConnector([ws])
    stop = asyncio.Event()

    runner = WebSocketCollectorRunner(
        source="bybit",
        stream="spot",
        url="wss://example.test/ws",
        connector=connector,
        subscription={"op": "subscribe", "args": ["publicTrade.BTCUSDT"]},
        parser=lambda message, received_at: [],
        event_sink=lambda event: None,
        incident_sink=lambda incident: None,
        heartbeat_message={"op": "ping"},
        heartbeat_interval_seconds=0.01,
    )

    task = asyncio.create_task(runner.run(stop))
    for _ in range(100):
        if '{"op":"ping"}' in ws.sent:
            break
        await asyncio.sleep(0.002)
    stop.set()
    await asyncio.wait_for(task, timeout=1)

    assert ws.sent[0] == '{"op":"subscribe","args":["publicTrade.BTCUSDT"]}'
    assert '{"op":"ping"}' in ws.sent


@pytest.mark.asyncio
async def test_runner_sends_dynamic_control_messages_without_reconnecting() -> None:
    ws = FakeWebSocket()
    connector = FakeConnector([ws])
    stop = asyncio.Event()
    outbound: asyncio.Queue[object] = asyncio.Queue()

    runner = WebSocketCollectorRunner(
        source="polymarket",
        stream="market",
        url="wss://example.test/ws",
        connector=connector,
        subscription={"assets_ids": ["old"], "type": "market"},
        parser=lambda message, received_at: [],
        event_sink=lambda event: None,
        incident_sink=lambda incident: None,
        heartbeat_message="PING",
        heartbeat_interval_seconds=30,
        outbound_messages=outbound,
    )

    task = asyncio.create_task(runner.run(stop))
    for _ in range(100):
        if ws.sent:
            break
        await asyncio.sleep(0.002)

    await outbound.put({"operation": "subscribe", "assets_ids": ["new"]})
    for _ in range(100):
        if '{"operation":"subscribe","assets_ids":["new"]}' in ws.sent:
            break
        await asyncio.sleep(0.002)

    stop.set()
    await asyncio.wait_for(task, timeout=1)

    assert connector.urls == ["wss://example.test/ws"]
    assert '{"operation":"subscribe","assets_ids":["new"]}' in ws.sent


@pytest.mark.asyncio
async def test_runner_cycles_with_full_subscription_for_replacement_control() -> None:
    first = FakeWebSocket()
    second = FakeWebSocket()
    connector = FakeConnector([first, second])
    stop = asyncio.Event()
    outbound: asyncio.Queue[object] = asyncio.Queue()

    runner = WebSocketCollectorRunner(
        source="polymarket",
        stream="market",
        url="wss://example.test/ws",
        connector=connector,
        subscription={"assets_ids": ["old"], "type": "market"},
        parser=lambda message, received_at: [],
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

    await outbound.put(
        {
            "_bp_control": "replace_market_subscription",
            "assets_ids": ["new", "old"],
        }
    )
    for _ in range(100):
        if len(connector.urls) >= 2:
            break
        await asyncio.sleep(0.002)

    stop.set()
    await asyncio.wait_for(task, timeout=1)

    assert connector.urls == ["wss://example.test/ws", "wss://example.test/ws"]
    assert first.sent == ['{"assets_ids":["old"],"type":"market"}']
    assert second.sent[0] == '{"assets_ids":["new","old"],"type":"market"}'


@pytest.mark.asyncio
async def test_runner_rejects_unknown_internal_control_without_provider_leak() -> None:
    first = FakeWebSocket()
    second = FakeWebSocket(["{\"sequence\": 9}"])
    connector = FakeConnector([first, second])
    incidents: list[FeedIncident] = []
    stop = asyncio.Event()
    outbound: asyncio.Queue[object] = asyncio.Queue()

    def parser(message: object, received_at: datetime) -> list[RawEvent]:
        stop.set()
        return [raw_event(9, received_at)]

    runner = WebSocketCollectorRunner(
        source="polymarket",
        stream="market",
        url="wss://example.test/ws",
        connector=connector,
        subscription={"assets_ids": ["old"], "type": "market"},
        parser=parser,
        event_sink=lambda event: None,
        incident_sink=incidents.append,
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

    await outbound.put({"_bp_control": "unknown_control", "assets_ids": ["new"]})
    for _ in range(100):
        if len(connector.urls) >= 2:
            break
        await asyncio.sleep(0.002)

    if len(connector.urls) < 2:
        stop.set()
    await asyncio.wait_for(task, timeout=1)

    assert first.sent == ['{"assets_ids":["old"],"type":"market"}']
    assert connector.urls == ["wss://example.test/ws", "wss://example.test/ws"]
    assert second.sent[0] == '{"assets_ids":["old"],"type":"market"}'
    assert "error" in [incident.incident_type for incident in incidents]


@pytest.mark.asyncio
async def test_runner_sends_multiple_initial_subscriptions_without_client_heartbeat() -> None:
    ws = FakeWebSocket(['{"channel":"heartbeats","sequence_num":1,"events":[]}'])
    connector = FakeConnector([ws])
    stop = asyncio.Event()

    def parser(message: object, received_at: datetime) -> list[RawEvent]:
        stop.set()
        return []

    runner = WebSocketCollectorRunner(
        source="coinbase",
        stream="spot",
        url="wss://example.test/ws",
        connector=connector,
        subscription=[
            {"type": "subscribe", "product_ids": ["BTC-USD"], "channel": "level2"},
            {"type": "subscribe", "product_ids": ["BTC-USD"], "channel": "market_trades"},
            {"type": "subscribe", "channel": "heartbeats"},
        ],
        parser=parser,
        event_sink=lambda event: None,
        incident_sink=lambda incident: None,
        heartbeat_message=None,
        heartbeat_interval_seconds=None,
    )

    await asyncio.wait_for(runner.run(stop), timeout=1)

    assert ws.sent == [
        '{"type":"subscribe","product_ids":["BTC-USD"],"channel":"level2"}',
        '{"type":"subscribe","product_ids":["BTC-USD"],"channel":"market_trades"}',
        '{"type":"subscribe","channel":"heartbeats"}',
    ]
