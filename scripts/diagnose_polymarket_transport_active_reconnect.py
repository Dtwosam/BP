from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from bp_engine.polymarket.discovery import discover_btc_markets
from bp_engine.polymarket.gamma import GammaClient

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
HEARTBEAT_SECONDS = 10.0
ACTION_OFFSET_SECONDS = 120.0
MIN_ACTION_LEAD_SECONDS = 270.0
BASELINE_WARMUP_SECONDS = 240.0
FAILURE_WAIT_SECONDS = 100.0
RECOVERY_OBSERVE_SECONDS = 45.0


@dataclass
class Stats:
    frame_count: int = 0
    data_frame_count: int = 0
    byte_count: int = 0
    pong_count: int = 0
    closed: bool = False
    close_code: int | None = None
    close_reason: str | None = None
    closed_at: str | None = None
    last_seen: dict[str, float] = field(default_factory=dict)
    asset_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))


def _wire(payload: object) -> str:
    return json.dumps(payload, separators=(",", ":"))


def _action_at_least(now: datetime, minimum_lead_seconds: float) -> datetime:
    epoch = int(now.timestamp())
    five_start = (epoch // 300) * 300
    action_epoch = five_start + int(ACTION_OFFSET_SECONDS)
    while action_epoch - now.timestamp() < minimum_lead_seconds:
        action_epoch += 300
    return datetime.fromtimestamp(action_epoch, tz=UTC)


def _find_market(markets: list[object], horizon_seconds: int, start_epoch: int) -> object:
    matches = [
        market
        for market in markets
        if market.horizon_seconds == horizon_seconds
        and market.active
        and int(market.window_start_at.timestamp()) == start_epoch
    ]
    if not matches:
        raise RuntimeError(
            f"missing active {horizon_seconds}s market starting epoch {start_epoch}"
        )
    return matches[0]


def _assets(market: object) -> list[str]:
    return [market.up_token_id, market.down_token_id]


def _message_assets(raw: object) -> set[str]:
    if isinstance(raw, bytes):
        text = raw.decode("utf-8")
    elif isinstance(raw, str):
        text = raw
    else:
        return set()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return set()
    items = payload if isinstance(payload, list) else [payload]
    assets: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        asset_id = item.get("asset_id")
        if asset_id:
            assets.add(str(asset_id))
        changes = item.get("price_changes")
        if isinstance(changes, list):
            for change in changes:
                if isinstance(change, dict) and change.get("asset_id"):
                    assets.add(str(change["asset_id"]))
    return assets


async def _receive(websocket: object, stats: Stats) -> None:
    try:
        while True:
            raw = await websocket.recv(decode=False)
            observed = time.monotonic()
            stats.frame_count += 1
            stats.byte_count += (
                len(raw)
                if isinstance(raw, bytes)
                else len(str(raw).encode("utf-8"))
            )
            if raw in {b"PONG", "PONG"}:
                stats.pong_count += 1
                continue
            stats.data_frame_count += 1
            for asset in _message_assets(raw):
                stats.asset_counts[asset] += 1
                stats.last_seen[asset] = observed
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
            try:
                await websocket.send("PING")
            except ConnectionClosed as exc:
                stats.closed = True
                stats.close_code = exc.code
                stats.close_reason = exc.reason
                stats.closed_at = datetime.now(UTC).isoformat()
                return


async def _wait_until(timestamp: float, stats: Stats | None = None) -> None:
    while time.time() < timestamp:
        if stats is not None and stats.closed:
            return
        await asyncio.sleep(0.05)


async def _run_connection(
    initial_assets: list[str],
) -> tuple[object, Stats, asyncio.Event, asyncio.Task[None], asyncio.Task[None]]:
    websocket = await connect(
        WS_URL,
        ping_interval=None,
        close_timeout=5,
        max_queue=1024,
    )
    await websocket.send(
        _wire({"assets_ids": sorted(set(initial_assets)), "type": "market"})
    )
    stats = Stats()
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
    started_at = datetime.now(UTC)
    action_at = _action_at_least(started_at, MIN_ACTION_LEAD_SECONDS)
    action_epoch = int(action_at.timestamp())
    five_start_epoch = (action_epoch // 300) * 300
    fifteen_start_epoch = (action_epoch // 900) * 900
    open_at = action_at.timestamp() - BASELINE_WARMUP_SECONDS

    await _wait_until(open_at)
    markets = await discover_btc_markets(
        GammaClient(),
        datetime.now(UTC),
        horizons=("5m", "15m"),
        offsets=(-1, 0, 1, 2),
    )
    active_five = _find_market(markets, 300, five_start_epoch)
    active_fifteen = _find_market(markets, 900, fifteen_start_epoch)
    five_assets = _assets(active_five)
    fifteen_assets = _assets(active_fifteen)
    expected_assets = sorted(set(five_assets + fifteen_assets))

    first_ws, first_stats, first_stop, first_receiver, first_heartbeat = (
        await _run_connection(fifteen_assets)
    )
    action_sent = False
    action_sent_at: str | None = None
    failure_after_action = False
    time_to_failure_seconds: float | None = None
    action_mono: float | None = None

    try:
        await _wait_until(action_at.timestamp(), first_stats)
        if not first_stats.closed:
            action_mono = time.monotonic()
            await first_ws.send(
                _wire({"operation": "subscribe", "assets_ids": five_assets})
            )
            action_sent = True
            action_sent_at = datetime.now(UTC).isoformat()
            await _wait_until(
                action_at.timestamp() + FAILURE_WAIT_SECONDS,
                first_stats,
            )
            if first_stats.closed:
                failure_after_action = True
                time_to_failure_seconds = round(
                    time.monotonic() - action_mono,
                    3,
                )
    finally:
        await _cleanup(
            first_ws,
            first_stop,
            first_receiver,
            first_heartbeat,
        )

    recovery: dict[str, object] | None = None
    if failure_after_action:
        reconnect_started_at = datetime.now(UTC)
        reconnect_started_mono = time.monotonic()
        second_ws, second_stats, second_stop, second_receiver, second_heartbeat = (
            await _run_connection(expected_assets)
        )
        try:
            await _wait_until(
                time.time() + RECOVERY_OBSERVE_SECONDS,
                second_stats,
            )
        finally:
            finished_mono = time.monotonic()
            await _cleanup(
                second_ws,
                second_stop,
                second_receiver,
                second_heartbeat,
            )

        ages = {
            asset: (
                None
                if asset not in second_stats.last_seen
                else round(finished_mono - second_stats.last_seen[asset], 3)
            )
            for asset in expected_assets
        }
        counts = {
            asset: second_stats.asset_counts.get(asset, 0)
            for asset in expected_assets
        }
        recovery = {
            "reconnect_started_at": reconnect_started_at.isoformat(),
            "reconnect_elapsed_seconds": round(
                time.monotonic() - reconnect_started_mono,
                3,
            ),
            "closed": second_stats.closed,
            "close_code": second_stats.close_code,
            "close_reason": second_stats.close_reason,
            "frame_count": second_stats.frame_count,
            "data_frame_count": second_stats.data_frame_count,
            "byte_count": second_stats.byte_count,
            "pong_count": second_stats.pong_count,
            "asset_counts": counts,
            "asset_ages_seconds": ages,
            "all_expected_assets_resumed": all(counts[asset] > 0 for asset in expected_assets),
            "all_expected_assets_fresh_10s": all(
                ages[asset] is not None and ages[asset] <= 10.0
                for asset in expected_assets
            ),
        }

    print(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "mode": "busy_active_add_then_full_set_reconnect",
                "action_target_at": action_at.isoformat(),
                "active_five_slug": active_five.slug,
                "active_fifteen_slug": active_fifteen.slug,
                "baseline_warmup_seconds": BASELINE_WARMUP_SECONDS,
                "action_sent": action_sent,
                "action_sent_at": action_sent_at,
                "first_connection": {
                    "closed": first_stats.closed,
                    "close_code": first_stats.close_code,
                    "close_reason": first_stats.close_reason,
                    "closed_at": first_stats.closed_at,
                    "frame_count": first_stats.frame_count,
                    "data_frame_count": first_stats.data_frame_count,
                    "byte_count": first_stats.byte_count,
                    "pong_count": first_stats.pong_count,
                    "failure_after_action": failure_after_action,
                    "time_to_failure_seconds": time_to_failure_seconds,
                },
                "expected_assets": expected_assets,
                "recovery": recovery,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
