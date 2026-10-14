import asyncio
import json
from collections.abc import Sequence
from datetime import datetime

import pytest

from bp_engine.collectors.reliability import ReconnectPolicy
from bp_engine.collectors.websocket_runner import WebSocketCollectorRunner
from bp_engine.recorder.models import FeedIncident, RawEvent


class FakeWebSocket:
    def __init__(
        self,
        *,
        messages: Sequence[object] = (),
        stop: asyncio.Event | None = None,
        stop_on_first_send: bool = False,
        yield_on_send_number: int | None = None,
        fail_on_send_number: int | None = None,
    ) -> None:
        self.messages: asyncio.Queue[object] = asyncio.Queue()
        for message in messages:
            self.messages.put_nowait(message)
        self.stop = stop
        self.stop_on_first_send = stop_on_first_send
        self.yield_on_send_number = yield_on_send_number
        self.fail_on_send_number = fail_on_send_number
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)
        if self.yield_on_send_number == len(self.sent):
            await asyncio.sleep(0.003)
        if self.fail_on_send_number == len(self.sent):
            raise RuntimeError("send failed")
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


@pytest.mark.asyncio
async def test_failed_receive_leaves_second_queued_outbound_subscription_unconsumed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop = asyncio.Event()
    first = FakeWebSocket(
        messages=[RuntimeError("connection ended")],
        yield_on_send_number=2,
    )
    first_control = {"operation": "subscribe", "assets_ids": ["next-a"]}
    second_control = {"operation": "subscribe", "assets_ids": ["next-b"]}
    outbound: asyncio.Queue[object] = asyncio.Queue()
    outbound.put_nowait(first_control)
    outbound.put_nowait(second_control)
    incidents: list[FeedIncident] = []
    original_wait = asyncio.wait
    wait_calls = 0

    async def simultaneous_first_wait(fs, *, return_when):
        nonlocal wait_calls
        wait_calls += 1
        if wait_calls == 1:
            await asyncio.sleep(0.003)
        return await original_wait(fs, return_when=return_when)

    monkeypatch.setattr(asyncio, "wait", simultaneous_first_wait)

    runner = WebSocketCollectorRunner(
        source="polymarket",
        stream="market",
        url="wss://example.test/ws",
        connector=SequenceConnector([]),
        subscription={"type": "market", "assets_ids": ["current"]},
        parser=lambda message, received_at: [],
        event_sink=lambda event: None,
        incident_sink=incidents.append,
        heartbeat_message=None,
        heartbeat_interval_seconds=None,
        outbound_messages=outbound,
        reconnect_policy=ReconnectPolicy(initial_seconds=0, maximum_seconds=0),
    )

    with pytest.raises(RuntimeError, match="connection ended"):
        await asyncio.wait_for(runner._run_connection(first, stop), timeout=1)

    assert json.loads(first.sent[0])["assets_ids"] == ["current"]
    assert json.loads(first.sent[1]) == first_control
    assert runner.subscription == {"type": "market", "assets_ids": ["current", "next-a"]}
    assert outbound.qsize() == 1
    assert outbound.get_nowait() == second_control


@pytest.mark.asyncio
async def test_ready_outbound_subscription_is_retained_when_heartbeat_send_fails_same_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop = asyncio.Event()
    first = FakeWebSocket(fail_on_send_number=2)
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
            # Let both the heartbeat timer and Queue.get() complete before
            # arbitration so a failed heartbeat send cannot hide queue loss.
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
        heartbeat_message="PING",
        heartbeat_interval_seconds=0.001,
        outbound_messages=outbound,
        reconnect_policy=ReconnectPolicy(initial_seconds=0, maximum_seconds=0),
    )

    await asyncio.wait_for(runner.run(stop), timeout=1)

    assert json.loads(first.sent[0])["assets_ids"] == ["current"]
    assert first.sent[1] == "PING"
    assert len(second.sent) == 1
    reconnect_subscription = json.loads(second.sent[0])
    assert reconnect_subscription["assets_ids"] == ["current", "next"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_phase", ["parser", "async_sink"])
async def test_ready_outbound_subscription_survives_same_turn_frame_processing_failure(
    monkeypatch: pytest.MonkeyPatch,
    failure_phase: str,
) -> None:
    """A consumed control must survive parser and awaited sink failures."""
    stop = asyncio.Event()
    first = FakeWebSocket(messages=[{"sequence": 1}])
    second = FakeWebSocket(stop=stop, stop_on_first_send=True)
    connector = SequenceConnector([first, second])
    outbound: asyncio.Queue[object] = asyncio.Queue()
    outbound.put_nowait({"operation": "subscribe", "assets_ids": ["next"]})
    incidents: list[FeedIncident] = []
    original_wait = asyncio.wait
    waits = 0

    async def simultaneous_first_wait(fs, *, return_when):
        nonlocal waits
        waits += 1
        if waits == 1:
            # The first receive and outbound Queue.get have completed.
            await asyncio.sleep(0.003)
        return await original_wait(fs, return_when=return_when)

    monkeypatch.setattr(asyncio, "wait", simultaneous_first_wait)

    def parser(message: object, received_at: datetime) -> list[RawEvent]:
        if failure_phase == "parser":
            raise RuntimeError("fixed parser failure")
        assert isinstance(message, dict)
        return [
            RawEvent.build(
                source="polymarket",
                stream="market",
                instrument="BTC",
                event_type="trade",
                source_timestamp=received_at,
                received_at=received_at,
                sequence=message["sequence"],
                payload={"synthetic": True},
            )
        ]

    async def fail_sink(_event: RawEvent) -> None:
        await asyncio.sleep(0)
        raise RuntimeError("fixed async sink failure")

    runner = WebSocketCollectorRunner(
        source="polymarket",
        stream="market",
        url="wss://example.test/ws",
        connector=connector,
        subscription={"type": "market", "assets_ids": ["current"]},
        parser=parser,
        event_sink=fail_sink,
        incident_sink=incidents.append,
        heartbeat_message=None,
        heartbeat_interval_seconds=None,
        outbound_messages=outbound,
        reconnect_policy=ReconnectPolicy(initial_seconds=0, maximum_seconds=0),
    )

    await asyncio.wait_for(runner.run(stop), timeout=1)

    assert [incident.incident_type for incident in incidents] == [
        "connected", "error", "reconnect", "disconnected",
        "connected", "disconnected",
    ]
    assert incidents[1].details["error_type"] == "RuntimeError"
    assert len(first.sent) == 1  # no control send after parse/sink failure
    assert json.loads(first.sent[0])["assets_ids"] == ["current"]
    assert len(second.sent) == 1
    assert json.loads(second.sent[0])["assets_ids"] == ["current", "next"]
    assert runner.subscription["assets_ids"] == ["current", "next"]
    assert outbound.empty()
