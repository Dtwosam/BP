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

from bp_engine.collectors.polymarket_ws import parse_polymarket_message
from bp_engine.polymarket.discovery import discover_btc_markets
from bp_engine.polymarket.gamma import GammaClient
from bp_engine.recorder.state import MarketStateReducer

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
HEARTBEAT_SECONDS = 10.0
REFRESH_OFFSET_SECONDS = 5.0
GRACE_SECONDS = 30.0
POST_UNSUBSCRIBE_SECONDS = 90.0
DrainMode = Literal["raw", "json", "bp_path"]


@dataclass
class Stats:
    frame_count: int = 0
    data_frame_count: int = 0
    pong_count: int = 0
    byte_count: int = 0
    processing_seconds_total: float = 0.0
    processing_seconds_max: float = 0.0
    recv_gap_seconds_max: float = 0.0
    last_recv_mono: float | None = None
    closed: bool = False
    close_code: int | None = None
    close_reason: str | None = None
    closed_at: str | None = None


def _wire(payload: object) -> str:
    return json.dumps(payload, separators=(",", ":"))


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


def _choose_future_15m(markets: list[object], now: datetime) -> tuple[object, object]:
    future = sorted(
        [
            market
            for market in markets
            if market.horizon_seconds == 900 and market.active and market.window_start_at > now
        ],
        key=lambda market: market.window_start_at,
    )
    if len(future) < 2:
        raise RuntimeError("need at least two future 15m markets")
    return future[0], future[-1]


def _next_5m_boundary(now: datetime) -> datetime:
    epoch = int(now.timestamp())
    next_start = ((epoch // 300) + 1) * 300
    return datetime.fromtimestamp(next_start, tz=UTC)


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


async def _receive(websocket: object, stats: Stats, mode: DrainMode) -> None:
    reducer = MarketStateReducer() if mode == "bp_path" else None
    try:
        while True:
            raw = await websocket.recv(decode=False)
            received_mono = time.monotonic()
            stats.frame_count += 1
            if stats.last_recv_mono is not None:
                stats.recv_gap_seconds_max = max(
                    stats.recv_gap_seconds_max,
                    received_mono - stats.last_recv_mono,
                )
            stats.last_recv_mono = received_mono
            if isinstance(raw, bytes):
                stats.byte_count += len(raw)
            elif isinstance(raw, str):
                stats.byte_count += len(raw.encode("utf-8"))

            if raw in {b"PONG", "PONG"}:
                stats.pong_count += 1
                continue

            stats.data_frame_count += 1
            processing_started = time.perf_counter()
            if mode == "raw":
                pass
            else:
                text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    payload = None
                if mode == "bp_path" and payload is not None:
                    received_at = datetime.now(UTC)
                    events = parse_polymarket_message(payload, received_at=received_at)
                    assert reducer is not None
                    for event in events:
                        reducer.observe(event)
            processing_seconds = time.perf_counter() - processing_started
            stats.processing_seconds_total += processing_seconds
            stats.processing_seconds_max = max(
                stats.processing_seconds_max,
                processing_seconds,
            )
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


async def main() -> None:
    mode = os.environ.get("POLYMARKET_DRAIN_MODE", "raw")
    if mode not in {"raw", "json", "bp_path"}:
        raise ValueError(f"unsupported POLYMARKET_DRAIN_MODE: {mode}")
    typed_mode: DrainMode = mode  # type: ignore[assignment]

    gamma = GammaClient()
    discovered_at = datetime.now(UTC)
    markets = await discover_btc_markets(
        gamma,
        discovered_at,
        horizons=("5m", "15m"),
        offsets=(-1, 0, 1, 2, 3, 4),
    )
    current_five = _choose_current_5m(markets, discovered_at)
    target, anchor = _choose_future_15m(markets, discovered_at)

    current_five_assets = [current_five.up_token_id, current_five.down_token_id]
    target_assets = [target.up_token_id, target.down_token_id]
    anchor_assets = [anchor.up_token_id, anchor.down_token_id]
    initial_assets = sorted(set(current_five_assets + anchor_assets))

    boundary = _next_5m_boundary(discovered_at)
    rotate_at = boundary.timestamp() + REFRESH_OFFSET_SECONDS
    unsubscribe_at = rotate_at + GRACE_SECONDS
    stop_at = unsubscribe_at + POST_UNSUBSCRIBE_SECONDS

    stats = Stats()
    stop = asyncio.Event()
    rotate_record: dict[str, object] = {}

    async with connect(
        WS_URL,
        ping_interval=None,
        close_timeout=5,
        max_queue=1024,
    ) as websocket:
        await websocket.send(_wire({"assets_ids": initial_assets, "type": "market"}))
        await websocket.send(
            _wire({"operation": "subscribe", "assets_ids": target_assets})
        )
        target_subscribed_at = datetime.now(UTC)

        receiver = asyncio.create_task(_receive(websocket, stats, typed_mode))
        heartbeat = asyncio.create_task(_heartbeat(websocket, stats, stop))
        try:
            while time.time() < rotate_at and not stats.closed:
                await asyncio.sleep(0.1)

            if not stats.closed:
                now = datetime.now(UTC)
                refreshed = await discover_btc_markets(
                    gamma,
                    now,
                    horizons=("5m",),
                    offsets=(-1, 0, 1),
                )
                next_five = _choose_current_5m(refreshed, now)
                next_assets = [next_five.up_token_id, next_five.down_token_id]
                subscribe_sent = await _send(
                    websocket,
                    stats,
                    {"operation": "subscribe", "assets_ids": next_assets},
                )
                rotate_record.update(
                    {
                        "boundary": boundary.isoformat(),
                        "subscribe_at": datetime.now(UTC).isoformat(),
                        "from_slug": current_five.slug,
                        "to_slug": next_five.slug,
                        "subscribe_sent": subscribe_sent,
                    }
                )

                while time.time() < unsubscribe_at and not stats.closed:
                    await asyncio.sleep(0.1)
                unsubscribe_sent = False
                if not stats.closed:
                    unsubscribe_sent = await _send(
                        websocket,
                        stats,
                        {
                            "operation": "unsubscribe",
                            "assets_ids": current_five_assets,
                        },
                    )
                rotate_record.update(
                    {
                        "unsubscribe_at": datetime.now(UTC).isoformat(),
                        "unsubscribe_sent": unsubscribe_sent,
                        "closed_before_unsubscribe": stats.closed,
                    }
                )

            while time.time() < stop_at and not stats.closed:
                await asyncio.sleep(0.1)
        finally:
            stop.set()
            for task in (receiver, heartbeat):
                if not task.done():
                    task.cancel()
            await asyncio.gather(receiver, heartbeat, return_exceptions=True)

    mean_processing = (
        stats.processing_seconds_total / stats.data_frame_count
        if stats.data_frame_count
        else 0.0
    )
    print(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "mode": typed_mode,
                "target_slug": target.slug,
                "anchor_slug": anchor.slug,
                "target_subscribed_at": target_subscribed_at.isoformat(),
                "target_subscription_offset_seconds": round(
                    (target_subscribed_at - target.window_start_at).total_seconds(), 3
                ),
                "initial_asset_count": len(initial_assets),
                "post_target_asset_count": len(set(initial_assets + target_assets)),
                "rotation": rotate_record,
                "frame_count": stats.frame_count,
                "data_frame_count": stats.data_frame_count,
                "pong_count": stats.pong_count,
                "byte_count": stats.byte_count,
                "processing_seconds_total": round(stats.processing_seconds_total, 6),
                "processing_seconds_mean": round(mean_processing, 9),
                "processing_seconds_max": round(stats.processing_seconds_max, 6),
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
