from __future__ import annotations

import asyncio
import inspect
import json
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

from bp_engine.collectors.reliability import ClockSkewGuard, FeedWatchdog, ReconnectPolicy
from bp_engine.recorder.models import FeedIncident, RawEvent


class WebSocketLike(Protocol):
    async def send(self, message: str) -> None: ...

    async def recv(self) -> object: ...


Parser = Callable[[object, datetime], list[RawEvent]]
EventSink = Callable[[RawEvent], Awaitable[None] | None]
IncidentSink = Callable[[FeedIncident], Awaitable[None] | None]
NowFactory = Callable[[], datetime]
MonotonicFactory = Callable[[], float]


def _wire_message(payload: object) -> str:
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, sort_keys=False, separators=(",", ":"))


def _decode_message(message: object) -> object:
    if isinstance(message, bytes):
        message = message.decode("utf-8")
    if not isinstance(message, str):
        return message
    try:
        return json.loads(message)
    except json.JSONDecodeError:
        return message


async def _call_sink(sink: Callable[[Any], Awaitable[None] | None], value: Any) -> None:
    result = sink(value)
    if inspect.isawaitable(result):
        await result


class WebSocketCollectorRunner:
    """Run one public WebSocket feed with heartbeat, reconnect, and incidents."""

    def __init__(
        self,
        *,
        source: str,
        stream: str,
        url: str,
        connector: Callable[[str], Any],
        subscription: Mapping[str, object] | str | Sequence[Mapping[str, object] | str],
        parser: Parser,
        event_sink: EventSink,
        incident_sink: IncidentSink,
        heartbeat_message: Mapping[str, object] | str | None,
        heartbeat_interval_seconds: float | None,
        reconnect_policy: ReconnectPolicy | None = None,
        now: NowFactory | None = None,
        outbound_messages: asyncio.Queue[object] | None = None,
        watchdog: FeedWatchdog | None = None,
        clock_skew_guard: ClockSkewGuard | None = None,
        monotonic: MonotonicFactory | None = None,
    ) -> None:
        if (heartbeat_message is None) != (heartbeat_interval_seconds is None):
            raise ValueError("heartbeat message and interval must be configured together")
        if heartbeat_interval_seconds is not None and heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be greater than zero")
        self.source = source
        self.stream = stream
        self.url = url
        self.connector = connector
        self.subscription = subscription
        self.parser = parser
        self.event_sink = event_sink
        self.incident_sink = incident_sink
        self.heartbeat_message = heartbeat_message
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.reconnect_policy = reconnect_policy or ReconnectPolicy()
        self.now = now or (lambda: datetime.now(UTC))
        self.outbound_messages = outbound_messages
        self.watchdog = watchdog
        self.clock_skew_guard = clock_skew_guard
        self.monotonic = monotonic or time.monotonic

    async def _incident(self, incident_type: str, details: dict[str, object]) -> None:
        await _call_sink(
            self.incident_sink,
            FeedIncident(
                source=self.source,
                stream=self.stream,
                incident_type=incident_type,
                observed_at=self.now(),
                details=details,
            ),
        )

    async def _run_connection(self, websocket: WebSocketLike, stop: asyncio.Event) -> None:
        subscriptions = (
            [self.subscription]
            if isinstance(self.subscription, (str, Mapping))
            else list(self.subscription)
        )
        for subscription in subscriptions:
            await websocket.send(_wire_message(subscription))
        recv_task = asyncio.create_task(websocket.recv())
        stop_task = asyncio.create_task(stop.wait())
        heartbeat_task = (
            asyncio.create_task(asyncio.sleep(self.heartbeat_interval_seconds))
            if self.heartbeat_interval_seconds is not None
            else None
        )
        outbound_task = (
            asyncio.create_task(self.outbound_messages.get())
            if self.outbound_messages is not None
            else None
        )
        watchdog_task = None
        if self.watchdog is not None:
            recovery = self.watchdog.observe(
                self.source,
                self.stream,
                monotonic_time=self.monotonic(),
                observed_at=self.now(),
            )
            if recovery is not None:
                await _call_sink(self.incident_sink, recovery)
            interval = max(min(self.watchdog.stale_after_seconds / 2, 1.0), 0.001)
            watchdog_task = asyncio.create_task(asyncio.sleep(interval))

        try:
            while True:
                wait_tasks = {recv_task, stop_task}
                if heartbeat_task is not None:
                    wait_tasks.add(heartbeat_task)
                if outbound_task is not None:
                    wait_tasks.add(outbound_task)
                if watchdog_task is not None:
                    wait_tasks.add(watchdog_task)
                done, _ = await asyncio.wait(
                    wait_tasks,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if stop_task in done and stop_task.result():
                    return

                if heartbeat_task is not None and heartbeat_task in done:
                    assert self.heartbeat_message is not None
                    assert self.heartbeat_interval_seconds is not None
                    await websocket.send(_wire_message(self.heartbeat_message))
                    heartbeat_task = asyncio.create_task(
                        asyncio.sleep(self.heartbeat_interval_seconds)
                    )

                if outbound_task is not None and outbound_task in done:
                    await websocket.send(_wire_message(outbound_task.result()))
                    outbound_task = asyncio.create_task(self.outbound_messages.get())

                if watchdog_task is not None and watchdog_task in done:
                    assert self.watchdog is not None
                    incident = self.watchdog.check(
                        self.source,
                        self.stream,
                        monotonic_time=self.monotonic(),
                        observed_at=self.now(),
                    )
                    if incident is not None:
                        await _call_sink(self.incident_sink, incident)
                    interval = max(
                        min(self.watchdog.stale_after_seconds / 2, 1.0),
                        0.001,
                    )
                    watchdog_task = asyncio.create_task(asyncio.sleep(interval))

                if recv_task in done:
                    message = recv_task.result()
                    received_at = self.now()
                    if self.watchdog is not None:
                        recovery = self.watchdog.observe(
                            self.source,
                            self.stream,
                            monotonic_time=self.monotonic(),
                            observed_at=received_at,
                        )
                        if recovery is not None:
                            await _call_sink(self.incident_sink, recovery)
                    events = self.parser(_decode_message(message), received_at)
                    for event in events:
                        if self.clock_skew_guard is not None:
                            incident = self.clock_skew_guard.check(
                                source=self.source,
                                stream=self.stream,
                                source_timestamp=event.source_timestamp,
                                received_at=event.received_at,
                            )
                            if incident is not None:
                                await _call_sink(self.incident_sink, incident)
                        await _call_sink(self.event_sink, event)
                    if stop.is_set():
                        return
                    recv_task = asyncio.create_task(websocket.recv())
        finally:
            tasks = [recv_task, stop_task]
            if heartbeat_task is not None:
                tasks.append(heartbeat_task)
            if outbound_task is not None:
                tasks.append(outbound_task)
            if watchdog_task is not None:
                tasks.append(watchdog_task)
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _wait_before_reconnect(self, stop: asyncio.Event, delay: float) -> None:
        if delay <= 0:
            await asyncio.sleep(0)
            return
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except TimeoutError:
            return

    async def run(self, stop: asyncio.Event) -> None:
        attempt = 0
        while not stop.is_set():
            connected = False
            try:
                async with self.connector(self.url) as websocket:
                    connected = True
                    await self._incident("connected", {"url": self.url})
                    await self._run_connection(websocket, stop)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._incident(
                    "error",
                    {"error_type": type(exc).__name__, "message": str(exc)},
                )
                if stop.is_set():
                    break
                delay = self.reconnect_policy.delay_for_attempt(attempt)
                await self._incident("reconnect", {"attempt": attempt, "delay_seconds": delay})
                attempt += 1
                await self._wait_before_reconnect(stop, delay)
            finally:
                if connected:
                    await self._incident(
                        "disconnected",
                        {"reason": "stop" if stop.is_set() else "connection_ended"},
                    )
