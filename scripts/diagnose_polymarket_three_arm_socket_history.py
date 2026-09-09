from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from bp_engine.polymarket.discovery import discover_btc_markets
from bp_engine.polymarket.gamma import GammaClient

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
AGE_SECONDS = 13.5 * 60
PROBE_SECONDS = 23 * 60
HEARTBEAT_SECONDS = 10
ROTATION_CHECK_SECONDS = 15
SNAPSHOT_SECONDS = 60


@dataclass
class StreamStats:
    counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    last_seen: dict[str, float] = field(default_factory=dict)
    data_frames: int = 0
    ping_count: int = 0
    pong_count: int = 0
    closed: bool = False
    close_code: int | None = None
    close_reason: str | None = None
    closed_at: str | None = None


def _wire(payload: object) -> str:
    return json.dumps(payload, separators=(",", ":"))


def _asset_ids(payload: object) -> set[str]:
    messages = payload if isinstance(payload, list) else [payload]
    assets: set[str] = set()
    for message in messages:
        if not isinstance(message, dict):
            continue
        asset_id = message.get("asset_id")
        if asset_id:
            assets.add(str(asset_id))
        changes = message.get("price_changes")
        if isinstance(changes, list):
            for change in changes:
                if isinstance(change, dict) and change.get("asset_id"):
                    assets.add(str(change["asset_id"]))
    return assets


def _choose_current_5m(markets: list[object], now: datetime) -> object:
    candidates = [
        market
        for market in markets
        if market.horizon_seconds == 300
        and market.active
        and market.window_start_at <= now <= market.window_end_at
    ]
    if not candidates:
        raise RuntimeError("no current 5m market available")
    return max(candidates, key=lambda market: market.window_start_at)


def _choose_future_15m(markets: list[object], now: datetime) -> object:
    probe_end = now + timedelta(seconds=PROBE_SECONDS)
    candidates = [
        market
        for market in markets
        if market.horizon_seconds == 900
        and market.active
        and market.window_start_at > now
        and market.window_end_at >= probe_end
    ]
    if not candidates:
        raise RuntimeError("no future 15m market spans the full probe window")
    return min(candidates, key=lambda market: market.window_start_at)


def _ages(stats: StreamStats, assets: list[str], now_mono: float) -> list[float | None]:
    return [
        None if asset not in stats.last_seen else round(now_mono - stats.last_seen[asset], 3)
        for asset in assets
    ]


async def _receive(websocket: object, stats: StreamStats) -> None:
    try:
        while True:
            raw = await websocket.recv(decode=False)
            if raw in {b"PONG", "PONG"}:
                stats.pong_count += 1
                continue
            stats.data_frames += 1
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            try:
                payload = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError:
                continue
            observed = time.monotonic()
            for asset in _asset_ids(payload):
                stats.counts[asset] += 1
                stats.last_seen[asset] = observed
    except ConnectionClosed as exc:
        stats.closed = True
        stats.close_code = exc.code
        stats.close_reason = exc.reason
        stats.closed_at = datetime.now(UTC).isoformat()


async def _send(websocket: object, stats: StreamStats, payload: object) -> bool:
    if stats.closed:
        return False
    try:
        await websocket.send(payload if isinstance(payload, str) else _wire(payload))
    except ConnectionClosed as exc:
        stats.closed = True
        stats.close_code = exc.code
        stats.close_reason = exc.reason
        stats.closed_at = datetime.now(UTC).isoformat()
        return False
    return True


async def _open_stream(
    stack: AsyncExitStack,
    assets: list[str],
    stats: StreamStats,
) -> tuple[object, asyncio.Task[None], datetime, float]:
    websocket = await stack.enter_async_context(
        connect(
            WS_URL,
            ping_interval=None,
            close_timeout=5,
            max_queue=1024,
        )
    )
    connected_at = datetime.now(UTC)
    connected_mono = time.monotonic()
    await websocket.send(_wire({"assets_ids": assets, "type": "market"}))
    receiver = asyncio.create_task(_receive(websocket, stats))
    return websocket, receiver, connected_at, connected_mono


async def main() -> None:
    gamma = GammaClient()
    started_at = datetime.now(UTC)
    initial_markets = await discover_btc_markets(
        gamma,
        started_at,
        horizons=("5m",),
        offsets=(-1, 0, 1),
    )
    initial_five = _choose_current_5m(initial_markets, started_at)
    initial_assets = [initial_five.up_token_id, initial_five.down_token_id]

    static_stats = StreamStats()
    rotating_stats = StreamStats()
    fresh_stats = StreamStats()
    rotations: list[dict[str, object]] = []
    snapshots: list[dict[str, object]] = []
    target = None
    target_assets: list[str] = []
    target_subscribed_at: datetime | None = None
    target_subscribed_mono: float | None = None

    async with AsyncExitStack() as stack:
        static_ws, static_receiver, static_connected_at, static_connected_mono = (
            await _open_stream(stack, initial_assets, static_stats)
        )
        rotating_ws, rotating_receiver, rotating_connected_at, rotating_connected_mono = (
            await _open_stream(stack, initial_assets, rotating_stats)
        )
        receivers: list[asyncio.Task[None]] = [static_receiver, rotating_receiver]

        current_rotating_market = initial_five
        current_rotating_assets = list(initial_assets)
        age_start_mono = max(static_connected_mono, rotating_connected_mono)
        age_stop_mono = age_start_mono + AGE_SECONDS
        next_ping = age_start_mono + HEARTBEAT_SECONDS
        next_rotation_check = age_start_mono + ROTATION_CHECK_SECONDS

        while time.monotonic() < age_stop_mono:
            now_mono = time.monotonic()
            if static_stats.closed and rotating_stats.closed:
                break

            if now_mono >= next_ping:
                if await _send(static_ws, static_stats, "PING"):
                    static_stats.ping_count += 1
                if await _send(rotating_ws, rotating_stats, "PING"):
                    rotating_stats.ping_count += 1
                next_ping += HEARTBEAT_SECONDS

            if now_mono >= next_rotation_check and not rotating_stats.closed:
                now_wall = datetime.now(UTC)
                refreshed = await discover_btc_markets(
                    gamma,
                    now_wall,
                    horizons=("5m",),
                    offsets=(-1, 0, 1),
                )
                new_five = _choose_current_5m(refreshed, now_wall)
                if new_five.slug != current_rotating_market.slug:
                    new_assets = [new_five.up_token_id, new_five.down_token_id]
                    subscribed = await _send(
                        rotating_ws,
                        rotating_stats,
                        {"operation": "subscribe", "assets_ids": new_assets},
                    )
                    unsubscribed = await _send(
                        rotating_ws,
                        rotating_stats,
                        {
                            "operation": "unsubscribe",
                            "assets_ids": current_rotating_assets,
                        },
                    )
                    rotations.append(
                        {
                            "at": now_wall.isoformat(),
                            "socket_age_seconds": round(
                                now_mono - rotating_connected_mono,
                                3,
                            ),
                            "from_slug": current_rotating_market.slug,
                            "to_slug": new_five.slug,
                            "subscribe_sent": subscribed,
                            "unsubscribe_sent": unsubscribed,
                        }
                    )
                    current_rotating_market = new_five
                    current_rotating_assets = new_assets
                next_rotation_check += ROTATION_CHECK_SECONDS

            await asyncio.sleep(0.05)

        aged_at = datetime.now(UTC)
        future_markets = await discover_btc_markets(
            gamma,
            aged_at,
            horizons=("15m",),
            offsets=(1, 2, 3),
        )
        target = _choose_future_15m(future_markets, aged_at)
        target_assets = [target.up_token_id, target.down_token_id]

        fresh_markets = await discover_btc_markets(
            gamma,
            aged_at,
            horizons=("5m",),
            offsets=(-1, 0, 1),
        )
        fresh_five = _choose_current_5m(fresh_markets, aged_at)
        fresh_assets = [fresh_five.up_token_id, fresh_five.down_token_id]
        fresh_ws, fresh_receiver, fresh_connected_at, fresh_connected_mono = (
            await _open_stream(stack, fresh_assets, fresh_stats)
        )
        receivers.append(fresh_receiver)
        await asyncio.sleep(0.5)

        subscribe_frame = {"operation": "subscribe", "assets_ids": target_assets}
        subscribe_results = await asyncio.gather(
            _send(static_ws, static_stats, subscribe_frame),
            _send(rotating_ws, rotating_stats, subscribe_frame),
            _send(fresh_ws, fresh_stats, subscribe_frame),
        )
        target_subscribed_mono = time.monotonic()
        target_subscribed_at = datetime.now(UTC)

        await asyncio.sleep(0.5)
        await asyncio.gather(
            _send(
                static_ws,
                static_stats,
                {"operation": "unsubscribe", "assets_ids": initial_assets},
            ),
            _send(
                rotating_ws,
                rotating_stats,
                {"operation": "unsubscribe", "assets_ids": current_rotating_assets},
            ),
            _send(
                fresh_ws,
                fresh_stats,
                {"operation": "unsubscribe", "assets_ids": fresh_assets},
            ),
        )

        stop_at = target_subscribed_mono + PROBE_SECONDS
        next_snapshot = target_subscribed_mono + SNAPSHOT_SECONDS
        next_ping = target_subscribed_mono + HEARTBEAT_SECONDS

        while time.monotonic() < stop_at:
            now_mono = time.monotonic()
            if static_stats.closed and rotating_stats.closed and fresh_stats.closed:
                break

            if now_mono >= next_ping:
                if await _send(static_ws, static_stats, "PING"):
                    static_stats.ping_count += 1
                if await _send(rotating_ws, rotating_stats, "PING"):
                    rotating_stats.ping_count += 1
                if await _send(fresh_ws, fresh_stats, "PING"):
                    fresh_stats.ping_count += 1
                next_ping += HEARTBEAT_SECONDS

            if now_mono >= next_snapshot:
                snapshots.append(
                    {
                        "subscription_age_seconds": round(
                            now_mono - target_subscribed_mono,
                            3,
                        ),
                        "market_offset_seconds": round(
                            (datetime.now(UTC) - target.window_start_at).total_seconds(),
                            3,
                        ),
                        "static_counts": [
                            static_stats.counts[asset] for asset in target_assets
                        ],
                        "static_ages_seconds": _ages(
                            static_stats, target_assets, now_mono
                        ),
                        "rotating_counts": [
                            rotating_stats.counts[asset] for asset in target_assets
                        ],
                        "rotating_ages_seconds": _ages(
                            rotating_stats, target_assets, now_mono
                        ),
                        "fresh_counts": [
                            fresh_stats.counts[asset] for asset in target_assets
                        ],
                        "fresh_ages_seconds": _ages(
                            fresh_stats, target_assets, now_mono
                        ),
                        "static_closed": static_stats.closed,
                        "rotating_closed": rotating_stats.closed,
                        "fresh_closed": fresh_stats.closed,
                    }
                )
                next_snapshot += SNAPSHOT_SECONDS

            await asyncio.sleep(0.05)

        finished_mono = time.monotonic()
        finished_at = datetime.now(UTC)
        open_through_probe = {
            "static": not static_stats.closed,
            "rotating": not rotating_stats.closed,
            "fresh": not fresh_stats.closed,
        }

        for receiver in receivers:
            if not receiver.done():
                receiver.cancel()
        await asyncio.gather(*receivers, return_exceptions=True)

    assert target is not None
    assert target_subscribed_at is not None
    assert target_subscribed_mono is not None

    def arm_result(stats: StreamStats, arm: str) -> dict[str, object]:
        return {
            "target_counts": {asset: stats.counts[asset] for asset in target_assets},
            "target_ages_seconds": _ages(stats, target_assets, finished_mono),
            "data_frames": stats.data_frames,
            "ping_count": stats.ping_count,
            "pong_count": stats.pong_count,
            "closed": stats.closed,
            "close_code": stats.close_code,
            "close_reason": stats.close_reason,
            "closed_at": stats.closed_at,
            "connection_open_through_probe": open_through_probe[arm],
        }

    print(
        json.dumps(
            {
                "generated_at": finished_at.isoformat(),
                "mode": "three_arm_socket_history",
                "configured_age_seconds": AGE_SECONDS,
                "configured_probe_seconds": PROBE_SECONDS,
                "initial_bootstrap_slug": initial_five.slug,
                "static_connected_at": static_connected_at.isoformat(),
                "rotating_connected_at": rotating_connected_at.isoformat(),
                "fresh_connected_at": fresh_connected_at.isoformat(),
                "fresh_bootstrap_slug": fresh_five.slug,
                "static_socket_age_at_target_seconds": round(
                    target_subscribed_mono - static_connected_mono,
                    3,
                ),
                "rotating_socket_age_at_target_seconds": round(
                    target_subscribed_mono - rotating_connected_mono,
                    3,
                ),
                "fresh_socket_age_at_target_seconds": round(
                    target_subscribed_mono - fresh_connected_mono,
                    3,
                ),
                "target_slug": target.slug,
                "target_window_start": target.window_start_at.isoformat(),
                "target_window_end": target.window_end_at.isoformat(),
                "target_subscribed_at": target_subscribed_at.isoformat(),
                "target_subscription_offset_seconds": round(
                    (target_subscribed_at - target.window_start_at).total_seconds(),
                    3,
                ),
                "target_subscription_age_seconds": round(
                    finished_mono - target_subscribed_mono,
                    3,
                ),
                "subscribe_results": {
                    "static": subscribe_results[0],
                    "rotating": subscribe_results[1],
                    "fresh": subscribe_results[2],
                },
                "rotation_count": len(rotations),
                "rotations": rotations,
                "static": arm_result(static_stats, "static"),
                "rotating": arm_result(rotating_stats, "rotating"),
                "fresh": arm_result(fresh_stats, "fresh"),
                "snapshots": snapshots,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
