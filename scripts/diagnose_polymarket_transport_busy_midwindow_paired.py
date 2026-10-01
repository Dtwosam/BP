from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from bp_engine.polymarket.discovery import discover_btc_markets
from bp_engine.polymarket.gamma import GammaClient

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
HEARTBEAT_SECONDS = 10.0
ACTION_OFFSET_SECONDS = 120.0
OBSERVE_AFTER_ACTION_SECONDS = 90.0
MIN_ACTION_LEAD_SECONDS = 25.0


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


@dataclass
class SocketProbe:
    name: str
    websocket: object
    stats: Stats
    stop: asyncio.Event
    receiver: asyncio.Task[None]
    heartbeat: asyncio.Task[None]
    action_sent: bool = False
    action_at: str | None = None


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


async def _send(probe: SocketProbe, payload: object) -> bool:
    if probe.stats.closed:
        return False
    try:
        await probe.websocket.send(payload if isinstance(payload, str) else _wire(payload))
    except ConnectionClosed as exc:
        probe.stats.closed = True
        probe.stats.close_code = exc.code
        probe.stats.close_reason = exc.reason
        probe.stats.closed_at = datetime.now(UTC).isoformat()
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


async def _heartbeat(probe: SocketProbe) -> None:
    while not probe.stop.is_set() and not probe.stats.closed:
        try:
            await asyncio.wait_for(probe.stop.wait(), timeout=HEARTBEAT_SECONDS)
            return
        except TimeoutError:
            if not await _send(probe, "PING"):
                return


async def _open_probe(name: str, initial_assets: list[str]) -> SocketProbe:
    websocket = await connect(
        WS_URL,
        ping_interval=None,
        close_timeout=5,
        max_queue=1024,
    )
    stats = Stats()
    await websocket.send(
        _wire({"assets_ids": sorted(set(initial_assets)), "type": "market"})
    )
    stop = asyncio.Event()
    receiver = asyncio.create_task(_receive(websocket, stats))
    placeholder = asyncio.create_task(asyncio.sleep(0))
    probe = SocketProbe(name, websocket, stats, stop, receiver, placeholder)
    probe.heartbeat = asyncio.create_task(_heartbeat(probe))
    await placeholder
    return probe


async def _close_probe(probe: SocketProbe) -> None:
    probe.stop.set()
    for task in (probe.receiver, probe.heartbeat):
        if not task.done():
            task.cancel()
    await asyncio.gather(probe.receiver, probe.heartbeat, return_exceptions=True)
    try:
        await probe.websocket.close()
    except Exception:
        pass


async def _wait_until(timestamp: float) -> None:
    while time.time() < timestamp:
        await asyncio.sleep(0.05)


async def main() -> None:
    started_at = datetime.now(UTC)
    action_at = _target_action(started_at)
    active_five_start = action_at - timedelta(seconds=ACTION_OFFSET_SECONDS)
    future_five_start = active_five_start + timedelta(seconds=300)
    fifteen_start_epoch = (int(action_at.timestamp()) // 900) * 900
    active_fifteen_start = datetime.fromtimestamp(fifteen_start_epoch, tz=UTC)
    active_fifteen_end = active_fifteen_start + timedelta(seconds=900)
    if action_at + timedelta(seconds=OBSERVE_AFTER_ACTION_SECONDS) >= active_fifteen_end:
        action_at += timedelta(seconds=300)
        active_five_start = action_at - timedelta(seconds=ACTION_OFFSET_SECONDS)
        future_five_start = active_five_start + timedelta(seconds=300)
        fifteen_start_epoch = (int(action_at.timestamp()) // 900) * 900
        active_fifteen_start = datetime.fromtimestamp(fifteen_start_epoch, tz=UTC)
        active_fifteen_end = active_fifteen_start + timedelta(seconds=900)

    markets = await discover_btc_markets(
        GammaClient(),
        datetime.now(UTC),
        horizons=("5m", "15m"),
        offsets=(-1, 0, 1, 2, 3, 4),
    )
    active_five = _find_market(markets, 300, active_five_start)
    future_five = _find_market(markets, 300, future_five_start)
    active_fifteen = _find_market(markets, 900, active_fifteen_start)
    baseline_assets = _assets(active_fifteen)

    probes = await asyncio.gather(
        _open_probe("dynamic_active", baseline_assets),
        _open_probe("dynamic_future", baseline_assets),
        _open_probe("no_action", baseline_assets),
    )
    by_name = {probe.name: probe for probe in probes}
    opened_at = datetime.now(UTC)

    try:
        await _wait_until(action_at.timestamp())
        active_probe = by_name["dynamic_active"]
        future_probe = by_name["dynamic_future"]
        active_probe.action_sent = await _send(
            active_probe,
            {"operation": "subscribe", "assets_ids": _assets(active_five)},
        )
        active_probe.action_at = datetime.now(UTC).isoformat()
        future_probe.action_sent = await _send(
            future_probe,
            {"operation": "subscribe", "assets_ids": _assets(future_five)},
        )
        future_probe.action_at = datetime.now(UTC).isoformat()
        by_name["no_action"].action_at = datetime.now(UTC).isoformat()

        stop_at = action_at.timestamp() + OBSERVE_AFTER_ACTION_SECONDS
        await _wait_until(stop_at)
    finally:
        await asyncio.gather(*(_close_probe(probe) for probe in probes))

    print(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "opened_at": opened_at.isoformat(),
                "action_target_at": action_at.isoformat(),
                "action_market_offset_seconds": ACTION_OFFSET_SECONDS,
                "action_is_five_boundary": int(action_at.timestamp()) % 300 == 0,
                "action_is_fifteen_boundary": int(action_at.timestamp()) % 900 == 0,
                "active_five_slug": active_five.slug,
                "future_five_slug": future_five.slug,
                "active_fifteen_slug": active_fifteen.slug,
                "active_fifteen_window_start": active_fifteen.window_start_at.isoformat(),
                "active_fifteen_window_end": active_fifteen.window_end_at.isoformat(),
                "probes": {
                    probe.name: {
                        "action_sent": probe.action_sent,
                        "action_at": probe.action_at,
                        "frame_count": probe.stats.frame_count,
                        "data_frame_count": probe.stats.data_frame_count,
                        "pong_count": probe.stats.pong_count,
                        "byte_count": probe.stats.byte_count,
                        "recv_gap_seconds_max": round(
                            probe.stats.recv_gap_seconds_max, 6
                        ),
                        "closed": probe.stats.closed,
                        "close_code": probe.stats.close_code,
                        "close_reason": probe.stats.close_reason,
                        "closed_at": probe.stats.closed_at,
                    }
                    for probe in probes
                },
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
