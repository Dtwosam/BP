from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from bp_engine.polymarket.discovery import discover_btc_markets
from bp_engine.polymarket.gamma import GammaClient

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
HEARTBEAT_SECONDS = 10.0
OPEN_LEAD_SECONDS = 65.0
TARGET_DYNAMIC_LEAD_SECONDS = 50.0
ACTIVE_SUBSCRIBE_OFFSET_SECONDS = 5.0
OBSERVE_AFTER_ACTION_SECONDS = 70.0
MIN_BOUNDARY_LEAD_SECONDS = 150.0
Scenario = Literal["dynamic_active", "fresh_initial_active", "dynamic_future"]


@dataclass
class Stats:
    frame_count: int = 0
    data_frame_count: int = 0
    pong_count: int = 0
    byte_count: int = 0
    recv_gap_seconds_max: float = 0.0
    last_recv_mono: float | None = None
    closed: bool = False
    close_code: int | None = None
    close_reason: str | None = None
    closed_at: str | None = None


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
            if stats.last_recv_mono is not None:
                stats.recv_gap_seconds_max = max(
                    stats.recv_gap_seconds_max,
                    now_mono - stats.last_recv_mono,
                )
            stats.last_recv_mono = now_mono
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


async def _open_and_drain(
    initial_assets: list[str],
) -> tuple[object, Stats, asyncio.Event, asyncio.Task[None], asyncio.Task[None]]:
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


async def _close_tasks(
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
    scenario = os.environ.get("POLYMARKET_SUBSCRIBE_SCENARIO", "dynamic_active")
    if scenario not in {"dynamic_active", "fresh_initial_active", "dynamic_future"}:
        raise ValueError(f"unsupported POLYMARKET_SUBSCRIBE_SCENARIO: {scenario}")

    started_at = datetime.now(UTC)
    boundary = _boundary_at_least(started_at, MIN_BOUNDARY_LEAD_SECONDS)
    preopen_at = boundary.timestamp() - OPEN_LEAD_SECONDS
    target_add_at = boundary.timestamp() - TARGET_DYNAMIC_LEAD_SECONDS
    action_at = boundary.timestamp() + ACTIVE_SUBSCRIBE_OFFSET_SECONDS
    stop_at = action_at + OBSERVE_AFTER_ACTION_SECONDS

    await _wait_until(preopen_at)
    discovery_at = datetime.now(UTC)
    markets = await discover_btc_markets(
        GammaClient(),
        discovery_at,
        horizons=("5m", "15m"),
        offsets=(-1, 0, 1, 2, 3, 4),
    )

    old_five = _find_market(markets, 300, boundary.replace() - __import__("datetime").timedelta(seconds=300))
    active_five = _find_market(markets, 300, boundary)
    future_five = _find_market(
        markets,
        300,
        boundary + __import__("datetime").timedelta(seconds=300),
    )
    target = _find_market(markets, 900, boundary)
    anchor_candidates = [
        market
        for market in markets
        if market.horizon_seconds == 900
        and market.active
        and market.window_start_at > boundary
    ]
    if not anchor_candidates:
        raise RuntimeError("no future 15m anchor available")
    anchor = max(anchor_candidates, key=lambda market: market.window_start_at)

    old_five_assets = _assets(old_five)
    active_five_assets = _assets(active_five)
    future_five_assets = _assets(future_five)
    target_assets = _assets(target)
    anchor_assets = _assets(anchor)
    base_initial_assets = old_five_assets + anchor_assets

    action_record: dict[str, object] = {}
    websocket: object | None = None
    stats: Stats | None = None
    stop: asyncio.Event | None = None
    receiver: asyncio.Task[None] | None = None
    heartbeat: asyncio.Task[None] | None = None

    try:
        if scenario in {"dynamic_active", "dynamic_future"}:
            websocket, stats, stop, receiver, heartbeat = await _open_and_drain(
                base_initial_assets
            )
            await _wait_until(target_add_at, stats)
            target_sent = await _send(
                websocket,
                stats,
                {"operation": "subscribe", "assets_ids": target_assets},
            )
            action_record["target_subscribe_sent"] = target_sent
            action_record["target_subscribe_at"] = datetime.now(UTC).isoformat()
            await _wait_until(action_at, stats)
            chosen_assets = (
                active_five_assets if scenario == "dynamic_active" else future_five_assets
            )
            action_sent = await _send(
                websocket,
                stats,
                {"operation": "subscribe", "assets_ids": chosen_assets},
            )
            action_record["action"] = "dynamic_subscribe"
            action_record["action_sent"] = action_sent
            action_record["action_at"] = datetime.now(UTC).isoformat()
        else:
            await _wait_until(action_at)
            final_assets = base_initial_assets + target_assets + active_five_assets
            websocket, stats, stop, receiver, heartbeat = await _open_and_drain(final_assets)
            action_record["action"] = "fresh_initial_subscription"
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
            await _close_tasks(websocket, stop, receiver, heartbeat)

    assert stats is not None
    print(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "scenario": scenario,
                "boundary": boundary.isoformat(),
                "old_five_slug": old_five.slug,
                "active_five_slug": active_five.slug,
                "future_five_slug": future_five.slug,
                "target_slug": target.slug,
                "anchor_slug": anchor.slug,
                "action": action_record,
                "frame_count": stats.frame_count,
                "data_frame_count": stats.data_frame_count,
                "pong_count": stats.pong_count,
                "byte_count": stats.byte_count,
                "recv_gap_seconds_max": round(stats.recv_gap_seconds_max, 6),
                "closed": stats.closed,
                "close_code": stats.close_code,
                "close_reason": stats.close_reason,
                "closed_at": stats.closed_at,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
