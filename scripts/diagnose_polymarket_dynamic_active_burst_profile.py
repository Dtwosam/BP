from __future__ import annotations

import asyncio
import json
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from bp_engine.polymarket.discovery import discover_btc_markets
from bp_engine.polymarket.gamma import GammaClient

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
HEARTBEAT_SECONDS = 10.0
OPEN_LEAD_SECONDS = 65.0
TARGET_DYNAMIC_LEAD_SECONDS = 50.0
ACTION_OFFSET_SECONDS = 5.0
PROFILE_SECONDS = 15.0
MIN_BOUNDARY_LEAD_SECONDS = 150.0
Scenario = Literal["dynamic_active", "fresh_initial_active"]


@dataclass
class Bucket:
    frames: int = 0
    bytes: int = 0
    event_types: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    asset_events: dict[str, int] = field(default_factory=lambda: defaultdict(int))


@dataclass
class Stats:
    frame_count: int = 0
    byte_count: int = 0
    pong_count: int = 0
    closed: bool = False
    close_code: int | None = None
    close_reason: str | None = None
    closed_at: str | None = None
    action_started_mono: float | None = None
    buckets: dict[int, Bucket] = field(default_factory=dict)


def _wire(payload: object) -> str:
    return json.dumps(payload, separators=(",", ":"))


def _boundary_at_least(now: datetime, minimum_lead_seconds: float) -> datetime:
    epoch = int(now.timestamp())
    boundary_epoch = ((epoch // 900) + 1) * 900
    if boundary_epoch - now.timestamp() < minimum_lead_seconds:
        boundary_epoch += 900
    return datetime.fromtimestamp(boundary_epoch, tz=UTC)


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


def _assets(market: object) -> list[str]:
    return [market.up_token_id, market.down_token_id]


def _messages(raw: object) -> list[dict[str, object]]:
    if isinstance(raw, bytes):
        text = raw.decode("utf-8")
    elif isinstance(raw, str):
        text = raw
    else:
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    items = payload if isinstance(payload, list) else [payload]
    return [item for item in items if isinstance(item, dict)]


def _message_assets(message: dict[str, object]) -> set[str]:
    assets: set[str] = set()
    asset_id = message.get("asset_id")
    if asset_id:
        assets.add(str(asset_id))
    changes = message.get("price_changes")
    if isinstance(changes, list):
        for change in changes:
            if isinstance(change, dict) and change.get("asset_id"):
                assets.add(str(change["asset_id"]))
    return assets


async def _send(websocket: object, stats: Stats, payload: object) -> bool:
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


async def _receive(websocket: object, stats: Stats) -> None:
    try:
        while True:
            raw = await websocket.recv(decode=False)
            now_mono = time.monotonic()
            stats.frame_count += 1
            frame_bytes = len(raw) if isinstance(raw, bytes) else len(str(raw).encode("utf-8"))
            stats.byte_count += frame_bytes
            if raw in {b"PONG", "PONG"}:
                stats.pong_count += 1
                continue
            if stats.action_started_mono is None:
                continue
            second = int(now_mono - stats.action_started_mono)
            if second < 0 or second >= int(PROFILE_SECONDS):
                continue
            bucket = stats.buckets.setdefault(second, Bucket())
            bucket.frames += 1
            bucket.bytes += frame_bytes
            for message in _messages(raw):
                event_type = message.get("event_type")
                if isinstance(event_type, str) and event_type:
                    bucket.event_types[event_type] += 1
                for asset in _message_assets(message):
                    bucket.asset_events[asset] += 1
    except ConnectionClosed as exc:
        stats.closed = True
        stats.close_code = exc.code
        stats.close_reason = exc.reason
        stats.closed_at = datetime.now(UTC).isoformat()


async def _heartbeat(websocket: object, stats: Stats, stop: asyncio.Event) -> None:
    while not stop.is_set() and not stats.closed:
        try:
            await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_SECONDS)
            return
        except TimeoutError:
            if not await _send(websocket, stats, "PING"):
                return


async def _wait_until(timestamp: float, stats: Stats | None = None) -> None:
    while time.time() < timestamp:
        if stats is not None and stats.closed:
            return
        await asyncio.sleep(0.05)


async def _open(initial_assets: list[str]) -> tuple[object, Stats, asyncio.Event, asyncio.Task[None], asyncio.Task[None]]:
    websocket = await connect(
        WS_URL,
        ping_interval=None,
        close_timeout=5,
        max_queue=1024,
    )
    stats = Stats()
    await websocket.send(_wire({"assets_ids": sorted(set(initial_assets)), "type": "market"}))
    stop = asyncio.Event()
    receiver = asyncio.create_task(_receive(websocket, stats))
    heartbeat = asyncio.create_task(_heartbeat(websocket, stats, stop))
    return websocket, stats, stop, receiver, heartbeat


async def _cleanup(
    websocket: object,
    stop: asyncio.Event,
    receiver: asyncio.Task[None],
    heartbeat: asyncio.Task[None],
) -> None:
    stop.set()
    for task in (receiver, heartbeat):
        if not task.done():
            task.cancel()
    await asyncio.gather(receiver, heartbeat, return_exceptions=True)
    try:
        await websocket.close()
    except Exception:
        pass


async def main() -> None:
    scenario = os.environ.get("POLYMARKET_BURST_SCENARIO", "dynamic_active")
    if scenario not in {"dynamic_active", "fresh_initial_active"}:
        raise ValueError(f"unsupported POLYMARKET_BURST_SCENARIO: {scenario}")

    started_at = datetime.now(UTC)
    boundary = _boundary_at_least(started_at, MIN_BOUNDARY_LEAD_SECONDS)
    preopen_at = boundary.timestamp() - OPEN_LEAD_SECONDS
    target_add_at = boundary.timestamp() - TARGET_DYNAMIC_LEAD_SECONDS
    action_at = boundary.timestamp() + ACTION_OFFSET_SECONDS
    stop_at = action_at + PROFILE_SECONDS

    await _wait_until(preopen_at)
    markets = await discover_btc_markets(
        GammaClient(),
        datetime.now(UTC),
        horizons=("5m", "15m"),
        offsets=(-1, 0, 1, 2, 3, 4),
    )
    old_five = _find_market(markets, 300, boundary - timedelta(seconds=300))
    active_five = _find_market(markets, 300, boundary)
    target = _find_market(markets, 900, boundary)
    anchors = [
        market
        for market in markets
        if market.horizon_seconds == 900
        and market.active
        and market.window_start_at > boundary
    ]
    if not anchors:
        raise RuntimeError("no future 15m anchor available")
    anchor = max(anchors, key=lambda market: market.window_start_at)

    old_assets = _assets(old_five)
    active_assets = _assets(active_five)
    target_assets = _assets(target)
    anchor_assets = _assets(anchor)
    base_assets = old_assets + anchor_assets

    websocket: object | None = None
    stats: Stats | None = None
    stop: asyncio.Event | None = None
    receiver: asyncio.Task[None] | None = None
    heartbeat: asyncio.Task[None] | None = None
    action_record: dict[str, object] = {}

    try:
        if scenario == "dynamic_active":
            websocket, stats, stop, receiver, heartbeat = await _open(base_assets)
            await _wait_until(target_add_at, stats)
            action_record["target_subscribe_sent"] = await _send(
                websocket,
                stats,
                {"operation": "subscribe", "assets_ids": target_assets},
            )
            action_record["target_subscribe_at"] = datetime.now(UTC).isoformat()
            await _wait_until(action_at, stats)
            stats.action_started_mono = time.monotonic()
            action_record["action_sent"] = await _send(
                websocket,
                stats,
                {"operation": "subscribe", "assets_ids": active_assets},
            )
            action_record["action_at"] = datetime.now(UTC).isoformat()
        else:
            await _wait_until(action_at)
            websocket, stats, stop, receiver, heartbeat = await _open(
                base_assets + target_assets + active_assets
            )
            stats.action_started_mono = time.monotonic()
            action_record["action_sent"] = True
            action_record["action_at"] = datetime.now(UTC).isoformat()

        assert stats is not None
        await _wait_until(stop_at, stats)
    finally:
        if all(value is not None for value in (websocket, stop, receiver, heartbeat)):
            assert websocket is not None
            assert stop is not None
            assert receiver is not None
            assert heartbeat is not None
            await _cleanup(websocket, stop, receiver, heartbeat)

    assert stats is not None
    bucket_output = []
    for second in range(int(PROFILE_SECONDS)):
        bucket = stats.buckets.get(second, Bucket())
        bucket_output.append(
            {
                "second": second,
                "frames": bucket.frames,
                "bytes": bucket.bytes,
                "event_types": dict(bucket.event_types),
                "active_asset_events": {
                    asset: bucket.asset_events.get(asset, 0) for asset in active_assets
                },
                "target_asset_events": {
                    asset: bucket.asset_events.get(asset, 0) for asset in target_assets
                },
                "old_asset_events": {
                    asset: bucket.asset_events.get(asset, 0) for asset in old_assets
                },
            }
        )

    print(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "scenario": scenario,
                "boundary": boundary.isoformat(),
                "old_five_slug": old_five.slug,
                "active_five_slug": active_five.slug,
                "target_slug": target.slug,
                "anchor_slug": anchor.slug,
                "action": action_record,
                "closed": stats.closed,
                "close_code": stats.close_code,
                "close_reason": stats.close_reason,
                "closed_at": stats.closed_at,
                "frame_count": stats.frame_count,
                "byte_count": stats.byte_count,
                "pong_count": stats.pong_count,
                "buckets": bucket_output,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
