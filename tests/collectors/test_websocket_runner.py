import asyncio
import json
from datetime import UTC, datetime

import pytest

from bp_engine.collectors.bybit_ws import build_bybit_subscription, parse_bybit_message
from bp_engine.collectors.reliability import ClockSkewGuard, FeedWatchdog, ReconnectPolicy
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


@pytest.mark.asyncio
async def test_bybit_linear_bulk_watchdog_is_not_public_trade_topic_specific() -> None:
    """Order-book-only traffic refreshes a stream-wide watchdog despite no trades."""
    when = datetime(2026, 10, 12, 21, 0, tzinfo=UTC)
    book = json.dumps(
        {
            "topic": "orderbook.50.BTCUSDT",
            "type": "delta",
            "cts": int(when.timestamp() * 1000),
            "data": {"s": "BTCUSDT", "b": [], "a": []},
        }
    )
    ws = FakeWebSocket([book, book, book])
    connector = FakeConnector([ws])
    stop = asyncio.Event()
    events: list[RawEvent] = []
    incidents: list[FeedIncident] = []
    clock = iter((0.0, 4.0, 8.0, 12.0))
    watchdog = FeedWatchdog(stale_after_seconds=5)

    def parser(message: object, received_at: datetime) -> list[RawEvent]:
        parsed = parse_bybit_message(message, venue="linear", received_at=received_at)
        if len(events) + len(parsed) == 3:
            stop.set()
        return parsed

    runner = WebSocketCollectorRunner(
        source="bybit",
        stream="linear",
        url="wss://example.test/linear",
        connector=connector,
        subscription=build_bybit_subscription(
            ["orderbook.50.BTCUSDT", "publicTrade.BTCUSDT", "allLiquidation.BTCUSDT"]
        ),
        parser=parser,
        event_sink=events.append,
        incident_sink=incidents.append,
        heartbeat_message=None,
        heartbeat_interval_seconds=None,
        watchdog=watchdog,
        monotonic=lambda: next(clock),
        now=lambda: when,
    )
    await asyncio.wait_for(runner.run(stop), timeout=2)

    subscribed = json.loads(ws.sent[0])
    assert "publicTrade.BTCUSDT" in subscribed["args"]
    assert len(events) == 3
    assert all(event.event_type == "orderbook_delta" for event in events)
    assert not any(event.event_type == "trade" for event in events)
    # Twelve synthetic seconds since the first arm, yet the stream-wide
    # watchdog is fresh because order-book messages repeatedly arrived.
    assert (
        watchdog.check(
            "bybit", "linear", monotonic_time=13, observed_at=when
        )
        is None
    )
    stale = watchdog.check(
        "bybit", "linear", monotonic_time=18, observed_at=when
    )
    assert stale is not None and stale.incident_type == "stale"
    assert not any(i.incident_type == "stale" for i in incidents)


@pytest.mark.asyncio
async def test_bybit_subscription_failure_ack_has_no_topic_incident() -> None:
    """A fake rejected subscription ACK is ignored by the current parser/runner."""
    when = datetime(2026, 10, 12, 21, 0, tzinfo=UTC)
    failed_ack = json.dumps(
        {"op": "subscribe", "success": False, "ret_msg": "synthetic topic rejection"}
    )
    book = json.dumps(
        {
            "topic": "orderbook.50.BTCUSDT",
            "type": "snapshot",
            "cts": int(when.timestamp() * 1000),
            "data": {"s": "BTCUSDT", "b": [], "a": []},
        }
    )
    ws = FakeWebSocket([failed_ack, book])
    events: list[RawEvent] = []
    incidents: list[FeedIncident] = []
    stop = asyncio.Event()
    received_messages = []

    def parser(message: object, received_at: datetime) -> list[RawEvent]:
        received_messages.append(message)
        parsed = parse_bybit_message(message, venue="linear", received_at=received_at)
        if parsed:
            stop.set()
        return parsed

    runner = WebSocketCollectorRunner(
        source="bybit",
        stream="linear",
        url="wss://example.test/linear",
        connector=FakeConnector([ws]),
        subscription=build_bybit_subscription(
            ["orderbook.50.BTCUSDT", "publicTrade.BTCUSDT"]
        ),
        parser=parser,
        event_sink=events.append,
        incident_sink=incidents.append,
        heartbeat_message=None,
        heartbeat_interval_seconds=None,
        now=lambda: when,
    )
    await asyncio.wait_for(runner.run(stop), timeout=2)

    assert received_messages[0]["success"] is False
    assert [event.event_type for event in events] == ["orderbook_snapshot"]
    # No explicit subscription-topic rejection incident; this says
    # nothing about actual acknowledgements in the historical feed.
    assert not any(
        i.incident_type in {"error", "subscription_rejected", "stale"}
        for i in incidents
    )
    assert [i.incident_type for i in incidents] == ["connected", "disconnected"]


@pytest.mark.asyncio
async def test_bybit_linear_socket_incidents_identify_bulk_and_ticker_separately() -> None:
    """Both sockets share venue/URL, but their incident provenance differs."""
    when = datetime(2026, 10, 13, 0, 0, tzinfo=UTC)
    events: list[RawEvent] = []
    collected: dict[str, list[FeedIncident]] = {}

    for component, topics, message in (
        (
            "bybit_linear",
            ["orderbook.50.BTCUSDT", "publicTrade.BTCUSDT"],
            {
                "topic": "orderbook.50.BTCUSDT",
                "type": "snapshot",
                "cts": int(when.timestamp() * 1000),
                "data": {"s": "BTCUSDT", "b": [], "a": []},
            },
        ),
        (
            "bybit_linear_ticker",
            ["tickers.BTCUSDT"],
            {
                "topic": "tickers.BTCUSDT",
                "ts": int(when.timestamp() * 1000),
                "data": {"symbol": "BTCUSDT", "lastPrice": "60001"},
            },
        ),
    ):
        stop = asyncio.Event()
        ws = FakeWebSocket([json.dumps(message)])
        incidents: list[FeedIncident] = []

        def parser(value: object, at: datetime) -> list[RawEvent]:
            result = parse_bybit_message(value, venue="linear", received_at=at)
            stop.set()
            return result

        runner = WebSocketCollectorRunner(
            source="bybit",
            stream="linear",
            url="wss://example.test/linear",
            connector=FakeConnector([ws]),
            subscription=build_bybit_subscription(topics),
            parser=parser,
            event_sink=events.append,
            incident_sink=incidents.append,
            heartbeat_message=None,
            heartbeat_interval_seconds=None,
            now=lambda: when,
            component_name=component,
        )
        await asyncio.wait_for(runner.run(stop), timeout=2)
        assert json.loads(ws.sent[0])["args"] == sorted(topics)
        assert [incident.incident_type for incident in incidents] == [
            "connected",
            "disconnected",
        ]
        assert all(incident.source == "bybit" for incident in incidents)
        assert all(incident.stream == "linear" for incident in incidents)
        assert all(
            incident.details["recorder_component"] == component
            for incident in incidents
        )
        collected[component] = incidents

    assert [event.event_type for event in events] == [
        "orderbook_snapshot",
        "ticker",
    ]
    assert {
        incidents[0].details["recorder_component"]
        for incidents in collected.values()
    } == {"bybit_linear", "bybit_linear_ticker"}
    assert (
        collected["bybit_linear"][0].details["url"]
        == collected["bybit_linear_ticker"][0].details["url"]
    )


@pytest.mark.asyncio
async def test_named_runner_tags_clock_skew_and_watchdog_recovery_incidents() -> None:
    when = datetime(2026, 10, 13, 0, 0, tzinfo=UTC)
    clock = iter((3.0, 3.1))
    watchdog = FeedWatchdog(stale_after_seconds=1)
    watchdog.observe("bybit", "linear", monotonic_time=0, observed_at=when)
    assert watchdog.check(
        "bybit", "linear", monotonic_time=2, observed_at=when
    ) is not None

    ws = FakeWebSocket(
        [json.dumps({
            "topic": "tickers.BTCUSDT",
            "ts": int(when.timestamp() * 1000) + 4000,
            "data": {"symbol": "BTCUSDT", "lastPrice": "60001"},
        })]
    )
    stop = asyncio.Event()
    incidents: list[FeedIncident] = []

    def parser(value: object, at: datetime) -> list[RawEvent]:
        stop.set()
        return parse_bybit_message(value, venue="linear", received_at=at)

    runner = WebSocketCollectorRunner(
        source="bybit",
        stream="linear",
        url="wss://example.test/linear",
        connector=FakeConnector([ws]),
        subscription=build_bybit_subscription(["tickers.BTCUSDT"]),
        parser=parser,
        event_sink=lambda event: None,
        incident_sink=incidents.append,
        heartbeat_message=None,
        heartbeat_interval_seconds=None,
        now=lambda: when,
        watchdog=watchdog,
        clock_skew_guard=ClockSkewGuard(max_abs_skew_seconds=1),
        monotonic=lambda: next(clock),
        component_name="bybit_linear_ticker",
    )
    await asyncio.wait_for(runner.run(stop), timeout=2)
    assert "clock_skew" in [incident.incident_type for incident in incidents]
    assert "recovered" in [incident.incident_type for incident in incidents]
    assert all(
        incident.details["recorder_component"] == "bybit_linear_ticker"
        for incident in incidents
    )


def test_component_identity_must_not_be_blank() -> None:
    with pytest.raises(ValueError, match="component_name must be non-empty"):
        WebSocketCollectorRunner(
            source="bybit",
            stream="linear",
            url="wss://example.test/linear",
            connector=lambda url: None,
            subscription={"op": "subscribe", "args": ["tickers.BTCUSDT"]},
            parser=lambda message, at: [],
            event_sink=lambda event: None,
            incident_sink=lambda incident: None,
            heartbeat_message=None,
            heartbeat_interval_seconds=None,
            component_name=" ",
        )
