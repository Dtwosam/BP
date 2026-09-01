from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from bp_engine.polymarket.discovery import discover_btc_markets
from bp_engine.polymarket.gamma import GammaClient
from bp_engine.recorder.polymarket_coordinator import PolymarketSubscriptionCoordinator

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
HEARTBEAT_SECONDS = 10
REFRESH_SECONDS = 30
SNAPSHOT_SECONDS = 60
TARGET_WAIT_SECONDS = 18 * 60
PROBE_SECONDS = 23 * 60
GRACE_SECONDS = 30


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
        raise RuntimeError("no current 5m bootstrap market available")
    return max(candidates, key=lambda market: market.window_start_at)


def _choose_new_15m_target(
    markets: list[object],
    added: frozenset[str],
    now: datetime,
) -> object | None:
    candidates = []
    for market in markets:
        assets = {market.up_token_id, market.down_token_id}
        if (
            market.horizon_seconds == 900
            and market.active
            and market.window_start_at > now
            and assets.issubset(added)
        ):
            candidates.append(market)
    if not candidates:
        return None
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


async def main() -> None:
    gamma = GammaClient()
    latest_markets: list[object] = []

    async def discovery(now: datetime) -> list[object]:
        nonlocal latest_markets
        latest_markets = await discover_btc_markets(
            gamma,
            now,
            horizons=("5m", "15m"),
            offsets=(-1, 0, 1),
        )
        return latest_markets

    coordinator = PolymarketSubscriptionCoordinator(
        discovery,
        grace_seconds=GRACE_SECONDS,
    )
    started_at = datetime.now(UTC)
    initial = await coordinator.refresh(started_at)
    if not initial.current:
        raise RuntimeError("no initial Polymarket assets available")

    aged_stats = StreamStats()
    fresh_stats = StreamStats()
    target = None
    target_assets: list[str] = []
    target_detected_at: datetime | None = None
    target_subscribed_at: datetime | None = None
    target_subscribed_mono: float | None = None
    aged_connected_at: datetime | None = None
    aged_connected_mono: float | None = None
    fresh_connected_at: datetime | None = None
    fresh_bootstrap_slug: str | None = None
    fresh_socket = None
    fresh_receiver: asyncio.Task[None] | None = None
    snapshots: list[dict[str, object]] = []
    rotations: list[dict[str, object]] = []

    async with AsyncExitStack() as stack:
        aged_socket = await stack.enter_async_context(
            connect(
                WS_URL,
                ping_interval=None,
                close_timeout=5,
                max_queue=1024,
            )
        )
        aged_connected_at = datetime.now(UTC)
        aged_connected_mono = time.monotonic()
        await aged_socket.send(
            _wire({"assets_ids": sorted(initial.current), "type": "market"})
        )
        aged_receiver = asyncio.create_task(_receive(aged_socket, aged_stats))

        next_ping = aged_connected_mono + HEARTBEAT_SECONDS
        next_refresh = aged_connected_mono + REFRESH_SECONDS
        target_deadline = aged_connected_mono + TARGET_WAIT_SECONDS

        while target is None and time.monotonic() < target_deadline and not aged_stats.closed:
            now_mono = time.monotonic()
            if now_mono >= next_ping:
                if await _send(aged_socket, aged_stats, "PING"):
                    aged_stats.ping_count += 1
                next_ping += HEARTBEAT_SECONDS

            if now_mono >= next_refresh:
                now_wall = datetime.now(UTC)
                diff = await coordinator.refresh(now_wall)
                candidate = _choose_new_15m_target(latest_markets, diff.added, now_wall)

                if candidate is None:
                    if diff.added:
                        await _send(
                            aged_socket,
                            aged_stats,
                            {"operation": "subscribe", "assets_ids": sorted(diff.added)},
                        )
                    if diff.removed:
                        await _send(
                            aged_socket,
                            aged_stats,
                            {"operation": "unsubscribe", "assets_ids": sorted(diff.removed)},
                        )
                    if diff.added or diff.removed:
                        rotations.append(
                            {
                                "at": now_wall.isoformat(),
                                "socket_age_seconds": round(now_mono - aged_connected_mono, 3),
                                "added_count": len(diff.added),
                                "removed_count": len(diff.removed),
                                "target_add": False,
                            }
                        )
                    next_refresh += REFRESH_SECONDS
                    continue

                target = candidate
                target_assets = [target.up_token_id, target.down_token_id]
                target_asset_set = set(target_assets)
                target_detected_at = now_wall

                non_target_added = diff.added - target_asset_set
                if non_target_added:
                    await _send(
                        aged_socket,
                        aged_stats,
                        {"operation": "subscribe", "assets_ids": sorted(non_target_added)},
                    )

                bootstrap = _choose_current_5m(latest_markets, now_wall)
                bootstrap_assets = [bootstrap.up_token_id, bootstrap.down_token_id]
                fresh_bootstrap_slug = bootstrap.slug
                fresh_socket = await stack.enter_async_context(
                    connect(
                        WS_URL,
                        ping_interval=None,
                        close_timeout=5,
                        max_queue=1024,
                    )
                )
                fresh_connected_at = datetime.now(UTC)
                await fresh_socket.send(
                    _wire({"assets_ids": bootstrap_assets, "type": "market"})
                )
                fresh_receiver = asyncio.create_task(_receive(fresh_socket, fresh_stats))
                await asyncio.sleep(0.5)

                subscribe_frame = {"operation": "subscribe", "assets_ids": target_assets}
                aged_sent, fresh_sent = await asyncio.gather(
                    _send(aged_socket, aged_stats, subscribe_frame),
                    _send(fresh_socket, fresh_stats, subscribe_frame),
                )
                if not aged_sent or not fresh_sent:
                    break
                target_subscribed_mono = time.monotonic()
                target_subscribed_at = datetime.now(UTC)

                await asyncio.sleep(0.5)
                await _send(
                    fresh_socket,
                    fresh_stats,
                    {"operation": "unsubscribe", "assets_ids": bootstrap_assets},
                )
                if diff.removed:
                    await _send(
                        aged_socket,
                        aged_stats,
                        {"operation": "unsubscribe", "assets_ids": sorted(diff.removed)},
                    )
                rotations.append(
                    {
                        "at": target_subscribed_at.isoformat(),
                        "socket_age_seconds": round(
                            target_subscribed_mono - aged_connected_mono,
                            3,
                        ),
                        "added_count": len(diff.added),
                        "removed_count": len(diff.removed),
                        "target_add": True,
                        "target_slug": target.slug,
                    }
                )
                next_refresh += REFRESH_SECONDS

            await asyncio.sleep(0.05)

        if target is not None and target_subscribed_mono is not None and fresh_socket is not None:
            stop_at = target_subscribed_mono + PROBE_SECONDS
            next_snapshot = target_subscribed_mono + SNAPSHOT_SECONDS
            fresh_next_ping = target_subscribed_mono + HEARTBEAT_SECONDS

            while time.monotonic() < stop_at:
                now_mono = time.monotonic()
                if aged_stats.closed and fresh_stats.closed:
                    break

                if now_mono >= next_ping:
                    if await _send(aged_socket, aged_stats, "PING"):
                        aged_stats.ping_count += 1
                    next_ping += HEARTBEAT_SECONDS
                if now_mono >= fresh_next_ping:
                    if await _send(fresh_socket, fresh_stats, "PING"):
                        fresh_stats.ping_count += 1
                    fresh_next_ping += HEARTBEAT_SECONDS

                if now_mono >= next_refresh and not aged_stats.closed:
                    now_wall = datetime.now(UTC)
                    diff = await coordinator.refresh(now_wall)
                    if diff.added:
                        await _send(
                            aged_socket,
                            aged_stats,
                            {"operation": "subscribe", "assets_ids": sorted(diff.added)},
                        )
                    if diff.removed:
                        await _send(
                            aged_socket,
                            aged_stats,
                            {"operation": "unsubscribe", "assets_ids": sorted(diff.removed)},
                        )
                    if diff.added or diff.removed:
                        rotations.append(
                            {
                                "at": now_wall.isoformat(),
                                "socket_age_seconds": round(now_mono - aged_connected_mono, 3),
                                "added_count": len(diff.added),
                                "removed_count": len(diff.removed),
                                "target_add": False,
                            }
                        )
                    next_refresh += REFRESH_SECONDS

                if now_mono >= next_snapshot:
                    snapshots.append(
                        {
                            "subscription_age_seconds": round(
                                now_mono - target_subscribed_mono, 3
                            ),
                            "market_offset_seconds": round(
                                (datetime.now(UTC) - target.window_start_at).total_seconds(),
                                3,
                            ),
                            "aged_target_counts": [
                                aged_stats.counts[asset] for asset in target_assets
                            ],
                            "aged_target_ages_seconds": _ages(
                                aged_stats, target_assets, now_mono
                            ),
                            "fresh_target_counts": [
                                fresh_stats.counts[asset] for asset in target_assets
                            ],
                            "fresh_target_ages_seconds": _ages(
                                fresh_stats, target_assets, now_mono
                            ),
                            "aged_ping_count": aged_stats.ping_count,
                            "aged_pong_count": aged_stats.pong_count,
                            "fresh_ping_count": fresh_stats.ping_count,
                            "fresh_pong_count": fresh_stats.pong_count,
                            "aged_closed": aged_stats.closed,
                            "fresh_closed": fresh_stats.closed,
                        }
                    )
                    next_snapshot += SNAPSHOT_SECONDS

                await asyncio.sleep(0.05)

        probe_finished_mono = time.monotonic()
        probe_finished_at = datetime.now(UTC)
        aged_open_through_probe = not aged_stats.closed
        fresh_open_through_probe = fresh_socket is not None and not fresh_stats.closed

        for task in (aged_receiver, fresh_receiver):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *[task for task in (aged_receiver, fresh_receiver) if task is not None],
            return_exceptions=True,
        )

    target_subscription_age = (
        None
        if target_subscribed_mono is None
        else round(probe_finished_mono - target_subscribed_mono, 3)
    )
    aged_socket_age_at_target = (
        None
        if target_subscribed_mono is None or aged_connected_mono is None
        else round(target_subscribed_mono - aged_connected_mono, 3)
    )

    print(
        json.dumps(
            {
                "generated_at": probe_finished_at.isoformat(),
                "mode": "aged_vs_fresh_dynamic_15m_add",
                "started_at": started_at.isoformat(),
                "initial_asset_count": len(initial.current),
                "target_found": target is not None,
                "target_slug": None if target is None else target.slug,
                "target_window_start": (
                    None if target is None else target.window_start_at.isoformat()
                ),
                "target_window_end": (
                    None if target is None else target.window_end_at.isoformat()
                ),
                "target_detected_at": (
                    None if target_detected_at is None else target_detected_at.isoformat()
                ),
                "target_subscribed_at": (
                    None if target_subscribed_at is None else target_subscribed_at.isoformat()
                ),
                "target_subscription_offset_seconds": (
                    None
                    if target is None or target_subscribed_at is None
                    else round(
                        (target_subscribed_at - target.window_start_at).total_seconds(),
                        3,
                    )
                ),
                "target_subscription_age_seconds": target_subscription_age,
                "aged_connected_at": (
                    None if aged_connected_at is None else aged_connected_at.isoformat()
                ),
                "aged_socket_age_at_target_seconds": aged_socket_age_at_target,
                "fresh_connected_at": (
                    None if fresh_connected_at is None else fresh_connected_at.isoformat()
                ),
                "fresh_bootstrap_slug": fresh_bootstrap_slug,
                "aged": {
                    "target_counts": {
                        asset: aged_stats.counts[asset] for asset in target_assets
                    },
                    "target_ages_seconds": _ages(
                        aged_stats, target_assets, probe_finished_mono
                    ),
                    "data_frames": aged_stats.data_frames,
                    "ping_count": aged_stats.ping_count,
                    "pong_count": aged_stats.pong_count,
                    "closed": aged_stats.closed,
                    "close_code": aged_stats.close_code,
                    "close_reason": aged_stats.close_reason,
                    "closed_at": aged_stats.closed_at,
                    "connection_open_through_probe": aged_open_through_probe,
                },
                "fresh": {
                    "target_counts": {
                        asset: fresh_stats.counts[asset] for asset in target_assets
                    },
                    "target_ages_seconds": _ages(
                        fresh_stats, target_assets, probe_finished_mono
                    ),
                    "data_frames": fresh_stats.data_frames,
                    "ping_count": fresh_stats.ping_count,
                    "pong_count": fresh_stats.pong_count,
                    "closed": fresh_stats.closed,
                    "close_code": fresh_stats.close_code,
                    "close_reason": fresh_stats.close_reason,
                    "closed_at": fresh_stats.closed_at,
                    "connection_open_through_probe": fresh_open_through_probe,
                },
                "rotation_count": len(rotations),
                "rotations": rotations,
                "snapshots": snapshots,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
