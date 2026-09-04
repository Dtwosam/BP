from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Protocol

from bp_engine.collectors.polymarket_ws import build_subscription_update
from bp_engine.recorder.models import FeedIncident
from bp_engine.recorder.polymarket_coordinator import PolymarketSubscriptionCoordinator


class Runnable(Protocol):
    async def run(self, stop: asyncio.Event) -> None: ...


class RecorderService:
    """Supervise recorder components under one shared stop signal."""

    def __init__(self, components: Mapping[str, Runnable]) -> None:
        if not components:
            raise ValueError("at least one recorder component is required")
        self._components = dict(components)

    @property
    def component_names(self) -> frozenset[str]:
        return frozenset(self._components)

    async def run(self, stop: asyncio.Event) -> None:
        tasks = {
            asyncio.create_task(component.run(stop), name=f"recorder:{name}"): name
            for name, component in self._components.items()
        }
        stop_task = asyncio.create_task(stop.wait(), name="recorder:stop")

        try:
            done, _ = await asyncio.wait(
                {*tasks, stop_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if stop_task in done and stop_task.result():
                await asyncio.gather(*tasks, return_exceptions=False)
                return

            completed_components = [task for task in done if task is not stop_task]
            stop.set()
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, BaseException):
                    raise result
            names = ", ".join(tasks[task] for task in completed_components)
            raise RuntimeError(f"recorder component exited unexpectedly: {names}")
        finally:
            if not stop_task.done():
                stop_task.cancel()
            await asyncio.gather(stop_task, return_exceptions=True)
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


RunnerFactory = Callable[[frozenset[str], asyncio.Queue[object]], Runnable]
NowFactory = Callable[[], datetime]


class _PolymarketSubscriptionUpdater:
    def __init__(
        self,
        *,
        coordinator: PolymarketSubscriptionCoordinator,
        outbound_messages: asyncio.Queue[object],
        refresh_interval_seconds: float,
        now: NowFactory,
    ) -> None:
        self._coordinator = coordinator
        self._outbound_messages = outbound_messages
        self._refresh_interval_seconds = refresh_interval_seconds
        self._now = now

    async def _wait(self, stop: asyncio.Event) -> bool:
        try:
            await asyncio.wait_for(stop.wait(), timeout=self._refresh_interval_seconds)
        except TimeoutError:
            return False
        return True

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            if await self._wait(stop):
                return
            diff = await self._coordinator.refresh(self._now())
            if diff.added:
                await self._outbound_messages.put(
                    build_subscription_update("subscribe", sorted(diff.added))
                )
            if diff.removed:
                await self._outbound_messages.put(
                    build_subscription_update("unsubscribe", sorted(diff.removed))
                )


class PolymarketCollectorComponent:
    """Bootstrap rotating market assets, then run socket and refresh loop together."""

    def __init__(
        self,
        *,
        coordinator: PolymarketSubscriptionCoordinator,
        runner_factory: RunnerFactory,
        outbound_messages: asyncio.Queue[object],
        refresh_interval_seconds: float,
        now: NowFactory | None = None,
    ) -> None:
        if refresh_interval_seconds <= 0:
            raise ValueError("refresh_interval_seconds must be greater than zero")
        self._coordinator = coordinator
        self._runner_factory = runner_factory
        self._outbound_messages = outbound_messages
        self._refresh_interval_seconds = refresh_interval_seconds
        self._now = now or (lambda: datetime.now(UTC))

    async def run(self, stop: asyncio.Event) -> None:
        initial = await self._coordinator.refresh(self._now())
        if not initial.current:
            raise RuntimeError("no Polymarket assets available for initial subscription")

        runner = self._runner_factory(initial.current, self._outbound_messages)
        updater = _PolymarketSubscriptionUpdater(
            coordinator=self._coordinator,
            outbound_messages=self._outbound_messages,
            refresh_interval_seconds=self._refresh_interval_seconds,
            now=self._now,
        )
        await RecorderService({"socket": runner, "subscriptions": updater}).run(stop)


class _BatchWriterComponent:
    def __init__(self, writer: object) -> None:
        self._writer = writer

    async def run(self, stop: asyncio.Event) -> None:
        await self._writer.run(stop)


class _DatabaseSink:
    def __init__(self, engine: object, repository: object) -> None:
        self._engine = engine
        self._repository = repository

    async def write_events(self, events: list[object]) -> None:
        def write() -> None:
            with self._engine.begin() as connection:
                self._repository.insert_events(connection, events)

        await asyncio.to_thread(write)

    async def write_state_snapshots(self, snapshots: list[object]) -> None:
        def write() -> None:
            with self._engine.begin() as connection:
                self._repository.upsert_state_snapshots(connection, snapshots)

        await asyncio.to_thread(write)

    async def record_incident(self, incident: object) -> None:
        def write() -> None:
            with self._engine.begin() as connection:
                self._repository.record_incident(connection, incident)

        await asyncio.to_thread(write)


class _BufferedEventSink:
    def __init__(
        self,
        buffer: object,
        incident_sink: Callable[[object], object],
        *,
        state_reducer: object | None = None,
    ) -> None:
        self._buffer = buffer
        self._incident_sink = incident_sink
        self._state_reducer = state_reducer
        self._backpressure_lock = asyncio.Lock()
        self._backpressure_started_at: datetime | None = None
        self._backpressure_blocked_events = 0

    async def _record_incident(self, incident: object) -> None:
        result = self._incident_sink(incident)
        if asyncio.iscoroutine(result):
            await result

    async def _observe_backpressure(self, event: object) -> FeedIncident | None:
        async with self._backpressure_lock:
            self._backpressure_blocked_events += 1
            if self._backpressure_started_at is not None:
                return None
            self._backpressure_started_at = event.received_at
            started_at = self._backpressure_started_at

        return FeedIncident(
            source=event.source,
            stream=event.stream,
            incident_type="backpressure",
            observed_at=event.received_at,
            details={
                "queue_size": self._buffer.qsize(),
                "episode_started_at": started_at.isoformat(),
            },
        )

    async def _recover_backpressure(self, event: object) -> FeedIncident | None:
        async with self._backpressure_lock:
            started_at = self._backpressure_started_at
            if started_at is None:
                return None
            blocked_event_count = self._backpressure_blocked_events
            self._backpressure_started_at = None
            self._backpressure_blocked_events = 0

        recovered_at = event.received_at
        duration_seconds = max(0.0, (recovered_at - started_at).total_seconds())
        return FeedIncident(
            source=event.source,
            stream=event.stream,
            incident_type="backpressure_recovered",
            observed_at=recovered_at,
            details={
                "episode_started_at": started_at.isoformat(),
                "recovered_at": recovered_at.isoformat(),
                "duration_seconds": duration_seconds,
                "blocked_event_count": blocked_event_count,
            },
        )

    async def __call__(self, event: object) -> None:
        from bp_engine.recorder.writer import QueueBackpressure

        try:
            self._buffer.put_nowait(event)
        except QueueBackpressure:
            start_incident = await self._observe_backpressure(event)
            if start_incident is not None:
                await self._record_incident(start_incident)
            await self._buffer.put(event)
        else:
            recovery_incident = await self._recover_backpressure(event)
            if recovery_incident is not None:
                await self._record_incident(recovery_incident)

        if self._state_reducer is None:
            return
        try:
            self._state_reducer.observe(event)
        except Exception as exc:
            await self._record_incident(
                FeedIncident(
                    source=event.source,
                    stream=event.stream,
                    incident_type="state_reducer_error",
                    observed_at=event.received_at,
                    details={
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    },
                )
            )


def build_default_recorder_service(settings: object) -> RecorderService:
    """Assemble the recorder without opening network connections."""
    from sqlalchemy import create_engine
    from websockets.asyncio.client import connect

    from bp_engine.collectors.bybit_ws import build_bybit_subscription, parse_bybit_message
    from bp_engine.collectors.coinbase_ws import (
        build_coinbase_subscriptions,
        parse_coinbase_message,
    )
    from bp_engine.collectors.polymarket_ws import (
        build_market_subscription,
        parse_polymarket_message,
    )
    from bp_engine.collectors.reliability import ClockSkewGuard, FeedWatchdog
    from bp_engine.collectors.websocket_runner import WebSocketCollectorRunner
    from bp_engine.polymarket.gamma import GammaClient
    from bp_engine.polymarket.service import MarketDiscoveryService
    from bp_engine.recorder.polymarket_coordinator import PolymarketSubscriptionCoordinator
    from bp_engine.recorder.state import MarketStateReducer, MarketStateSnapshotter
    from bp_engine.recorder.writer import BatchWriter, EventBuffer
    from bp_engine.storage.partitioned_raw import (
        RawStorageMode,
        ensure_partitioned_raw_storage,
        raw_storage_mode,
    )
    from bp_engine.storage.recorder import RecorderRepository
    from bp_engine.storage.schema import metadata

    engine = create_engine(settings.database_url)
    metadata.create_all(engine)
    if engine.dialect.name == "postgresql":
        with engine.connect() as connection:
            storage_mode = raw_storage_mode(connection)
        if storage_mode is RawStorageMode.PARTITIONED:
            ensure_partitioned_raw_storage(
                engine,
                now=datetime.now(UTC),
                migrate_existing=False,
            )
    repository = RecorderRepository()
    database_sink = _DatabaseSink(engine, repository)
    buffer = EventBuffer(maxsize=settings.recorder_queue_maxsize)
    state_reducer = MarketStateReducer()
    event_sink = _BufferedEventSink(
        buffer,
        database_sink.record_incident,
        state_reducer=state_reducer,
    )
    writer = BatchWriter(
        buffer=buffer,
        sink=database_sink.write_events,
        batch_size=settings.recorder_batch_size,
        flush_interval_seconds=settings.recorder_flush_interval_seconds,
        worker_count=settings.recorder_writer_workers,
    )
    state_snapshotter = MarketStateSnapshotter(
        reducer=state_reducer,
        write_snapshots=database_sink.write_state_snapshots,
        interval_seconds=1.0,
        max_state_age_seconds=settings.recorder_stale_after_seconds,
    )

    discovery_service = MarketDiscoveryService(settings, GammaClient(), engine)
    coordinator = PolymarketSubscriptionCoordinator(
        discovery_service.discover_and_store,
        grace_seconds=settings.polymarket_subscription_grace_seconds,
    )
    outbound: asyncio.Queue[object] = asyncio.Queue()

    def polymarket_runner_factory(
        assets: frozenset[str], outbound_messages: asyncio.Queue[object]
    ) -> WebSocketCollectorRunner:
        return WebSocketCollectorRunner(
            source="polymarket",
            stream="market",
            url=settings.polymarket_ws_url,
            connector=connect,
            subscription=build_market_subscription(sorted(assets)),
            parser=lambda message, received_at: parse_polymarket_message(
                message, received_at=received_at
            ),
            event_sink=event_sink,
            incident_sink=database_sink.record_incident,
            heartbeat_message="PING",
            heartbeat_interval_seconds=10.0,
            outbound_messages=outbound_messages,
            watchdog=FeedWatchdog(settings.recorder_stale_after_seconds),
            clock_skew_guard=ClockSkewGuard(settings.recorder_max_clock_skew_seconds),
        )

    polymarket = PolymarketCollectorComponent(
        coordinator=coordinator,
        runner_factory=polymarket_runner_factory,
        outbound_messages=outbound,
        refresh_interval_seconds=settings.polymarket_refresh_interval_seconds,
    )

    spot_topics = ["orderbook.50.BTCUSDT", "publicTrade.BTCUSDT"]
    linear_topics = [
        "orderbook.50.BTCUSDT",
        "publicTrade.BTCUSDT",
        "tickers.BTCUSDT",
        "allLiquidation.BTCUSDT",
    ]
    bybit_spot = WebSocketCollectorRunner(
        source="bybit",
        stream="spot",
        url=settings.bybit_spot_ws_url,
        connector=connect,
        subscription=build_bybit_subscription(spot_topics),
        parser=lambda message, received_at: parse_bybit_message(
            message, venue="spot", received_at=received_at
        ),
        event_sink=event_sink,
        incident_sink=database_sink.record_incident,
        heartbeat_message={"op": "ping"},
        heartbeat_interval_seconds=20.0,
        watchdog=FeedWatchdog(settings.recorder_stale_after_seconds),
        clock_skew_guard=ClockSkewGuard(settings.recorder_max_clock_skew_seconds),
    )
    bybit_linear = WebSocketCollectorRunner(
        source="bybit",
        stream="linear",
        url=settings.bybit_linear_ws_url,
        connector=connect,
        subscription=build_bybit_subscription(linear_topics),
        parser=lambda message, received_at: parse_bybit_message(
            message, venue="linear", received_at=received_at
        ),
        event_sink=event_sink,
        incident_sink=database_sink.record_incident,
        heartbeat_message={"op": "ping"},
        heartbeat_interval_seconds=20.0,
        watchdog=FeedWatchdog(settings.recorder_stale_after_seconds),
        clock_skew_guard=ClockSkewGuard(settings.recorder_max_clock_skew_seconds),
    )

    coinbase_spot = WebSocketCollectorRunner(
        source="coinbase",
        stream="spot",
        url=settings.coinbase_spot_ws_url,
        connector=connect,
        subscription=build_coinbase_subscriptions(["BTC-USD"]),
        parser=lambda message, received_at: parse_coinbase_message(
            message, received_at=received_at
        ),
        event_sink=event_sink,
        incident_sink=database_sink.record_incident,
        heartbeat_message=None,
        heartbeat_interval_seconds=None,
        watchdog=FeedWatchdog(settings.recorder_stale_after_seconds),
        clock_skew_guard=ClockSkewGuard(settings.recorder_max_clock_skew_seconds),
    )

    return RecorderService(
        {
            "writer": _BatchWriterComponent(writer),
            "state_snapshotter": state_snapshotter,
            "polymarket": polymarket,
            "bybit_spot": bybit_spot,
            "bybit_linear": bybit_linear,
            "coinbase_spot": coinbase_spot,
        }
    )
