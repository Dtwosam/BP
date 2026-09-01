from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from bp_engine.collectors.polymarket_ws import build_subscription_update
from bp_engine.collectors.websocket_runner import WebSocketCollectorRunner
from bp_engine.polymarket.discovery import discover_btc_markets
from bp_engine.polymarket.gamma import GammaClient

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
HEARTBEAT_SECONDS = 10.0
ACTION_OFFSET_SECONDS = 120.0
OBSERVE_AFTER_ACTION_SECONDS = 120.0
MIN_ACTION_LEAD_SECONDS = 25.0


@dataclass
class TrackedSocket:
    connector: TrackingConnector
    index: int
    websocket: Any
    opened_mono: float = field(default_factory=time.monotonic)
    opened_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    sent: list[str] = field(default_factory=list)
    recv_count: int = 0
    target_recv_count: int = 0
    byte_count: int = 0
    provider_close_code: int | None = None
    provider_close_reason: str | None = None
    provider_closed_at: str | None = None
    provider_closed_mono: float | None = None

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

        now_mono = time.monotonic()
        self.recv_count += 1
        if isinstance(raw, bytes):
            self.byte_count += len(raw)
            text = raw.decode("utf-8", errors="ignore")
        elif isinstance(raw, str):
            self.byte_count += len(raw.encode("utf-8"))
            text = raw
        else:
            text = ""

        if any(asset in text for asset in self.connector.target_assets):
            self.target_recv_count += 1
            self.connector.target_observations.append(now_mono)
        return raw

    def _record_provider_close(self, exc: ConnectionClosed) -> None:
        if self.provider_closed_mono is not None:
            return
        self.provider_close_code = exc.code
        self.provider_close_reason = exc.reason
        self.provider_closed_at = datetime.now(UTC).isoformat()
        self.provider_closed_mono = time.monotonic()


class _TrackedContext:
    def __init__(self, connector: TrackingConnector, url: str) -> None:
        self.connector = connector
        self.url = url
        self.tracked: TrackedSocket | None = None

    async def __aenter__(self) -> TrackedSocket:
        # Intentionally use production websockets.connect defaults here.
        websocket = await connect(self.url)
        self.tracked = TrackedSocket(
            connector=self.connector,
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
    def __init__(self, name: str, target_assets: list[str]) -> None:
        self.name = name
        self.target_assets = tuple(target_assets)
        self.connections: list[TrackedSocket] = []
        self.target_observations: list[float] = []

    def __call__(self, url: str) -> _TrackedContext:
        return _TrackedContext(self, url)


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


def _relative(value: float | None, action_mono: float) -> float | None:
    return None if value is None else round(value - action_mono, 3)


def _arm_summary(
    connector: TrackingConnector,
    *,
    action_mono: float,
    finished_mono: float,
    replacement_assets: list[str],
    incidents: list[dict[str, object]],
) -> dict[str, object]:
    after_action = sorted(value for value in connector.target_observations if value >= action_mono)
    first_target_delay = (
        None if not after_action else round(after_action[0] - action_mono, 3)
    )
    final_target_age = (
        None if not after_action else round(finished_mono - after_action[-1], 3)
    )
    max_target_gap = None
    if len(after_action) >= 2:
        max_target_gap = round(
            max(
                second - first
                for first, second in zip(after_action, after_action[1:], strict=False)
            ),
            3,
        )

    full_set_connections = [
        tracked
        for tracked in connector.connections
        if _initial_assets(tracked) == replacement_assets
    ]
    first_full_set = full_set_connections[0] if full_set_connections else None
    provider_closures = [
        tracked for tracked in connector.connections if tracked.provider_closed_mono is not None
    ]

    return {
        "connection_count": len(connector.connections),
        "provider_closure_count": len(provider_closures),
        "provider_close_offsets_seconds": [
            _relative(tracked.provider_closed_mono, action_mono) for tracked in provider_closures
        ],
        "first_full_set_connection_offset_seconds": (
            None if first_full_set is None else _relative(first_full_set.opened_mono, action_mono)
        ),
        "first_target_data_delay_seconds": first_target_delay,
        "max_target_data_gap_seconds": max_target_gap,
        "final_target_data_age_seconds": final_target_age,
        "target_observation_count": len(after_action),
        "connections": [
            {
                "index": tracked.index,
                "opened_at": tracked.opened_at,
                "opened_offset_seconds": _relative(tracked.opened_mono, action_mono),
                "initial_assets": _initial_assets(tracked),
                "recv_count": tracked.recv_count,
                "target_recv_count": tracked.target_recv_count,
                "byte_count": tracked.byte_count,
                "provider_close_code": tracked.provider_close_code,
                "provider_close_reason": tracked.provider_close_reason,
                "provider_closed_at": tracked.provider_closed_at,
                "provider_close_offset_seconds": _relative(
                    tracked.provider_closed_mono, action_mono
                ),
                "sent": tracked.sent,
            }
            for tracked in connector.connections
        ],
        "incidents": incidents,
    }


async def _run_arm(
    *,
    name: str,
    connector: TrackingConnector,
    baseline_assets: list[str],
    outbound: asyncio.Queue[object],
    stop: asyncio.Event,
    incidents: list[dict[str, object]],
) -> None:
    async def incident_sink(incident: object) -> None:
        incidents.append(
            {
                "observed_at": getattr(incident, "observed_at", None).isoformat()
                if getattr(incident, "observed_at", None) is not None
                else None,
                "incident_type": getattr(incident, "incident_type", None),
                "details": getattr(incident, "details", None),
            }
        )

    runner = WebSocketCollectorRunner(
        source="polymarket",
        stream=name,
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
    await runner.run(stop)


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

    existing_connector = TrackingConnector("existing", active_five_assets)
    prototype_connector = TrackingConnector("prototype", active_five_assets)
    existing_outbound: asyncio.Queue[object] = asyncio.Queue()
    prototype_outbound: asyncio.Queue[object] = asyncio.Queue()
    existing_stop = asyncio.Event()
    prototype_stop = asyncio.Event()
    existing_incidents: list[dict[str, object]] = []
    prototype_incidents: list[dict[str, object]] = []

    tasks = [
        asyncio.create_task(
            _run_arm(
                name="existing_late_active_validation",
                connector=existing_connector,
                baseline_assets=baseline_assets,
                outbound=existing_outbound,
                stop=existing_stop,
                incidents=existing_incidents,
            )
        ),
        asyncio.create_task(
            _run_arm(
                name="prototype_late_active_validation",
                connector=prototype_connector,
                baseline_assets=baseline_assets,
                outbound=prototype_outbound,
                stop=prototype_stop,
                incidents=prototype_incidents,
            )
        ),
    ]

    action_mono = 0.0
    try:
        opened = await asyncio.gather(
            _wait_for_connections(existing_connector, 1, timeout=10.0),
            _wait_for_connections(prototype_connector, 1, timeout=10.0),
        )
        if opened != [True, True]:
            raise RuntimeError("both validation arms must open initial connections")

        await _wait_until(action_at.timestamp())
        if len(existing_connector.connections) != 1 or len(prototype_connector.connections) != 1:
            raise RuntimeError("validation arm reconnected before late-active action")

        action_mono = time.monotonic()
        await existing_outbound.put(
            build_subscription_update("subscribe", active_five_assets)
        )
        await prototype_outbound.put(
            {
                "_bp_control": "replace_market_subscription",
                "assets_ids": replacement_assets,
            }
        )

        await _wait_until(action_at.timestamp() + OBSERVE_AFTER_ACTION_SECONDS)
    finally:
        finished_mono = time.monotonic()
        existing_stop.set()
        prototype_stop.set()
        try:
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=20.0)
        except TimeoutError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    if action_mono <= 0:
        raise RuntimeError("late-active action was not executed")

    print(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "transport_profile": "production_websockets_connect_defaults",
                "action_target_at": action_at.isoformat(),
                "action_market_offset_seconds": ACTION_OFFSET_SECONDS,
                "observe_after_action_seconds": OBSERVE_AFTER_ACTION_SECONDS,
                "active_five_slug": active_five.slug,
                "active_fifteen_slug": active_fifteen.slug,
                "replacement_assets": replacement_assets,
                "existing": _arm_summary(
                    existing_connector,
                    action_mono=action_mono,
                    finished_mono=finished_mono,
                    replacement_assets=replacement_assets,
                    incidents=existing_incidents,
                ),
                "prototype": _arm_summary(
                    prototype_connector,
                    action_mono=action_mono,
                    finished_mono=finished_mono,
                    replacement_assets=replacement_assets,
                    incidents=prototype_incidents,
                ),
            },
            sort_keys=True,
            default=str,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
