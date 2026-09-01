from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from bp_engine.collectors.websocket_runner import WebSocketCollectorRunner
from bp_engine.polymarket.discovery import discover_btc_markets
from bp_engine.polymarket.gamma import GammaClient

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
HEARTBEAT_SECONDS = 10.0
ACTION_OFFSET_SECONDS = 120.0
OBSERVE_AFTER_ACTION_SECONDS = 90.0
MIN_ACTION_LEAD_SECONDS = 25.0


@dataclass
class DirectStats:
    frame_count: int = 0
    data_frame_count: int = 0
    pong_count: int = 0
    byte_count: int = 0
    closed: bool = False
    close_code: int | None = None
    close_reason: str | None = None
    closed_at: str | None = None


@dataclass
class TrackedSocket:
    index: int
    websocket: Any
    sent: list[str] = field(default_factory=list)
    recv_count: int = 0
    byte_count: int = 0
    provider_close_code: int | None = None
    provider_close_reason: str | None = None
    provider_closed_at: str | None = None

    async def send(self, message: str) -> None:
        self.sent.append(message)
        try:
            await self.websocket.send(message)
        except ConnectionClosed as exc:
            self._record_provider_close(exc)
            raise

    async def recv(self) -> object:
        try:
            raw = await self.websocket.recv()
        except ConnectionClosed as exc:
            self._record_provider_close(exc)
            raise
        self.recv_count += 1
        if isinstance(raw, bytes):
            self.byte_count += len(raw)
        elif isinstance(raw, str):
            self.byte_count += len(raw.encode("utf-8"))
        return raw

    def _record_provider_close(self, exc: ConnectionClosed) -> None:
        self.provider_close_code = exc.code
        self.provider_close_reason = exc.reason
        self.provider_closed_at = datetime.now(UTC).isoformat()


class _TrackedContext:
    def __init__(self, connector: TrackingConnector, url: str) -> None:
        self.connector = connector
        self.url = url
        self.tracked: TrackedSocket | None = None

    async def __aenter__(self) -> TrackedSocket:
        websocket = await connect(
            self.url,
            ping_interval=None,
            close_timeout=5,
            max_queue=1024,
        )
        self.tracked = TrackedSocket(
            index=len(self.connector.connections) + 1,
            websocket=websocket,
        )
        self.connector.connections.append(self.tracked)
        return self.tracked

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        assert self.tracked is not None
        try:
            await self.tracked.websocket.close()
        except Exception:
            pass


class TrackingConnector:
    def __init__(self) -> None:
        self.connections: list[TrackedSocket] = []

    def __call__(self, url: str) -> _TrackedContext:
        return _TrackedContext(self, url)


@dataclass
class DirectProbe:
    websocket: Any
    stats: DirectStats
    receiver: asyncio.Task[None]
    stop: asyncio.Event
    heartbeat: asyncio.Task[None]


def _wire(payload: object) -> str:
    return json.dumps(payload, separators=(",", ":"))


def _assets(market: object) -> list[str]:
    return [market.up_token_id, market.down_token_id]


def _target_action(now: datetime) -> datetime:
    epoch = int(now.timestamp())
    current_five_start = (epoch // 300) * 300
    action_epoch = current_five_start + int(ACTION_OFFSET_SECONDS)
    if action_epoch - now.timestamp() < MIN_ACTION_LEAD_SECONDS:
        action_epoch += 300
    return datetime.fromtimestamp(action_epoch, tz=UTC)


def _find_market(markets: list[object], horizon_seconds: int, start_at: datetime) -> object:
    matches = [
        market
        for market in markets
        if market.horizon_seconds == horizon_seconds
        and market.active
        and market.window_start_at == start_at
    ]
    if not matches:
        raise RuntimeError(
            f"missing active {horizon_seconds}s market starting {start_at.isoformat()}"
        )
    return matches[0]


async def _wait_until(timestamp: float) -> None:
    while time.time() < timestamp:
        await asyncio.sleep(0.05)


async def _direct_receive(websocket: Any, stats: DirectStats) -> None:
    try:
        while True:
            raw = await websocket.recv(decode=False)
            stats.frame_count += 1
            if isinstance(raw, bytes):
                stats.byte_count += len(raw)
            elif isinstance(raw, str):
                stats.byte_count += len(raw.encode("utf-8"))
            if raw in {b"PONG", "PONG"}:
                stats.pong_count += 1
            else:
                stats.data_frame_count += 1
    except ConnectionClosed as exc:
        stats.closed = True
        stats.close_code = exc.code
        stats.close_reason = exc.reason
        stats.closed_at = datetime.now(UTC).isoformat()


async def _direct_heartbeat(probe: DirectProbe) -> None:
    while not probe.stop.is_set() and not probe.stats.closed:
        try:
            await asyncio.wait_for(probe.stop.wait(), timeout=HEARTBEAT_SECONDS)
            return
        except TimeoutError:
            try:
                await probe.websocket.send("PING")
            except ConnectionClosed as exc:
                probe.stats.closed = True
                probe.stats.close_code = exc.code
                probe.stats.close_reason = exc.reason
                probe.stats.closed_at = datetime.now(UTC).isoformat()
                return


async def _open_direct(initial_assets: list[str]) -> DirectProbe:
    websocket = await connect(
        WS_URL,
        ping_interval=None,
        close_timeout=5,
        max_queue=1024,
    )
    await websocket.send(_wire({"assets_ids": sorted(set(initial_assets)), "type": "market"}))
    stats = DirectStats()
    stop = asyncio.Event()
    receiver = asyncio.create_task(_direct_receive(websocket, stats))
    placeholder = asyncio.create_task(asyncio.sleep(0))
    probe = DirectProbe(websocket, stats, receiver, stop, placeholder)
    probe.heartbeat = asyncio.create_task(_direct_heartbeat(probe))
    await placeholder
    return probe


async def _close_direct(probe: DirectProbe) -> None:
    probe.stop.set()
    for task in (probe.receiver, probe.heartbeat):
        if not task.done():
            task.cancel()
    await asyncio.gather(probe.receiver, probe.heartbeat, return_exceptions=True)
    try:
        await probe.websocket.close()
    except Exception:
        pass


async def _wait_for_connections(connector: TrackingConnector, count: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(connector.connections) >= count:
            return True
        await asyncio.sleep(0.05)
    return len(connector.connections) >= count


def _initial_assets(tracked: TrackedSocket) -> list[str] | None:
    if not tracked.sent:
        return None
    try:
        payload = json.loads(tracked.sent[0])
    except json.JSONDecodeError:
        return None
    assets = payload.get("assets_ids") if isinstance(payload, dict) else None
    if not isinstance(assets, list):
        return None
    return sorted(str(asset) for asset in assets)


async def main() -> None:
    started_at = datetime.now(UTC)
    action_at = _target_action(started_at)
    active_five_start = action_at - timedelta(seconds=ACTION_OFFSET_SECONDS)
    fifteen_start_epoch = (int(action_at.timestamp()) // 900) * 900
    active_fifteen_start = datetime.fromtimestamp(fifteen_start_epoch, tz=UTC)
    active_fifteen_end = active_fifteen_start + timedelta(seconds=900)
    if action_at + timedelta(seconds=OBSERVE_AFTER_ACTION_SECONDS) >= active_fifteen_end:
        action_at += timedelta(seconds=300)
        active_five_start = action_at - timedelta(seconds=ACTION_OFFSET_SECONDS)
        fifteen_start_epoch = (int(action_at.timestamp()) // 900) * 900
        active_fifteen_start = datetime.fromtimestamp(fifteen_start_epoch, tz=UTC)

    markets = await discover_btc_markets(
        GammaClient(),
        datetime.now(UTC),
        horizons=("5m", "15m"),
        offsets=(-1, 0, 1, 2, 3, 4),
    )
    active_five = _find_market(markets, 300, active_five_start)
    active_fifteen = _find_market(markets, 900, active_fifteen_start)
    baseline_assets = _assets(active_fifteen)
    active_five_assets = _assets(active_five)
    replacement_assets = sorted(set(baseline_assets + active_five_assets))

    direct = await _open_direct(baseline_assets)
    connector = TrackingConnector()
    outbound: asyncio.Queue[object] = asyncio.Queue()
    protected_stop = asyncio.Event()
    incidents: list[dict[str, object]] = []

    async def incident_sink(incident: object) -> None:
        incidents.append(
            {
                "incident_type": getattr(incident, "incident_type", None),
                "details": getattr(incident, "details", None),
            }
        )

    runner = WebSocketCollectorRunner(
        source="polymarket",
        stream="late_active_validation",
        url=WS_URL,
        connector=connector,
        subscription={"assets_ids": sorted(baseline_assets), "type": "market"},
        parser=lambda payload, received_at: [],
        event_sink=lambda event: None,
        incident_sink=incident_sink,
        heartbeat_message="PING",
        heartbeat_interval_seconds=HEARTBEAT_SECONDS,
        outbound_messages=outbound,
    )
    runner_task = asyncio.create_task(runner.run(protected_stop))

    direct_action_sent = False
    protected_control_queued_at: str | None = None
    second_connection_seen = False

    try:
        if not await _wait_for_connections(connector, 1, timeout=10.0):
            raise RuntimeError("protected runner did not open initial connection")
        await _wait_until(action_at.timestamp())

        try:
            await direct.websocket.send(
                _wire({"operation": "subscribe", "assets_ids": active_five_assets})
            )
            direct_action_sent = True
        except ConnectionClosed as exc:
            direct.stats.closed = True
            direct.stats.close_code = exc.code
            direct.stats.close_reason = exc.reason
            direct.stats.closed_at = datetime.now(UTC).isoformat()

        protected_control_queued_at = datetime.now(UTC).isoformat()
        await outbound.put(
            {
                "_bp_control": "replace_market_subscription",
                "assets_ids": replacement_assets,
            }
        )
        second_connection_seen = await _wait_for_connections(connector, 2, timeout=10.0)

        await _wait_until(action_at.timestamp() + OBSERVE_AFTER_ACTION_SECONDS)
    finally:
        protected_stop.set()
        try:
            await asyncio.wait_for(runner_task, timeout=10.0)
        except TimeoutError:
            runner_task.cancel()
            await asyncio.gather(runner_task, return_exceptions=True)
        await _close_direct(direct)

    second_initial_assets = (
        _initial_assets(connector.connections[1]) if len(connector.connections) >= 2 else None
    )
    second_provider_closed = (
        connector.connections[1].provider_close_code is not None
        if len(connector.connections) >= 2
        else None
    )

    print(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "action_target_at": action_at.isoformat(),
                "action_market_offset_seconds": ACTION_OFFSET_SECONDS,
                "active_five_slug": active_five.slug,
                "active_fifteen_slug": active_fifteen.slug,
                "replacement_assets": replacement_assets,
                "direct_control": {
                    "action_sent": direct_action_sent,
                    "frame_count": direct.stats.frame_count,
                    "data_frame_count": direct.stats.data_frame_count,
                    "pong_count": direct.stats.pong_count,
                    "byte_count": direct.stats.byte_count,
                    "closed": direct.stats.closed,
                    "close_code": direct.stats.close_code,
                    "close_reason": direct.stats.close_reason,
                    "closed_at": direct.stats.closed_at,
                },
                "protected": {
                    "control_queued_at": protected_control_queued_at,
                    "second_connection_seen": second_connection_seen,
                    "connection_count": len(connector.connections),
                    "second_initial_assets": second_initial_assets,
                    "second_initial_matches_replacement": second_initial_assets
                    == replacement_assets,
                    "second_provider_closed": second_provider_closed,
                    "connections": [
                        {
                            "index": tracked.index,
                            "sent": tracked.sent,
                            "recv_count": tracked.recv_count,
                            "byte_count": tracked.byte_count,
                            "provider_close_code": tracked.provider_close_code,
                            "provider_close_reason": tracked.provider_close_reason,
                            "provider_closed_at": tracked.provider_closed_at,
                        }
                        for tracked in connector.connections
                    ],
                    "incidents": incidents,
                },
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
