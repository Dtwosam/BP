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

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
PROBE_SECONDS = 23 * 60
HEARTBEAT_SECONDS = 10
ROTATION_CHECK_SECONDS = 15
SNAPSHOT_SECONDS = 60


@dataclass
class Stats:
    counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    last_seen: dict[str, float] = field(default_factory=dict)
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


def _choose_target_and_anchor(markets: list[object], now: datetime) -> tuple[object, object]:
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
    target = future[0]
    anchors = [
        market
        for market in future[1:]
        if (market.window_end_at - now).total_seconds() > PROBE_SECONDS + 120
    ]
    if not anchors:
        raise RuntimeError("no future 15m anchor spans the full probe")
    return target, anchors[-1]


def _snapshot(stats: Stats, target_assets: list[str], now_mono: float) -> dict[str, object]:
    ages = [
        None if asset not in stats.last_seen else round(now_mono - stats.last_seen[asset], 3)
        for asset in target_assets
    ]
    return {
        "counts": [stats.counts[asset] for asset in target_assets],
        "ages_seconds": ages,
        "closed": stats.closed,
    }


async def _receive(websocket: object, stats: Stats) -> None:
    try:
        while True:
            raw = await websocket.recv(decode=False)
            if raw in {b"PONG", "PONG"}:
                stats.pong_count += 1
                continue
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


async def main() -> None:
    gamma = GammaClient()
    discovered_at = datetime.now(UTC)
    markets = await discover_btc_markets(
        gamma,
        discovered_at,
        horizons=("5m", "15m"),
        offsets=(-1, 0, 1, 2, 3),
    )
    five = _choose_current_5m(markets, discovered_at)
    target, anchor = _choose_target_and_anchor(markets, discovered_at)

    five_assets = [five.up_token_id, five.down_token_id]
    target_assets = [target.up_token_id, target.down_token_id]
    anchor_assets = [anchor.up_token_id, anchor.down_token_id]
    initial_assets = sorted(set(five_assets + anchor_assets))

    control_stats = Stats()
    churn_stats = Stats()
    rotations: list[dict[str, object]] = []
    snapshots: list[dict[str, object]] = []

    async with AsyncExitStack() as stack:
        control_ws = await stack.enter_async_context(
            connect(WS_URL, ping_interval=None, close_timeout=5, max_queue=1024)
        )
        churn_ws = await stack.enter_async_context(
            connect(WS_URL, ping_interval=None, close_timeout=5, max_queue=1024)
        )
        await control_ws.send(_wire({"assets_ids": initial_assets, "type": "market"}))
        await churn_ws.send(_wire({"assets_ids": initial_assets, "type": "market"}))
        control_receiver = asyncio.create_task(_receive(control_ws, control_stats))
        churn_receiver = asyncio.create_task(_receive(churn_ws, churn_stats))

        target_frame = {"operation": "subscribe", "assets_ids": target_assets}
        subscribe_results = await asyncio.gather(
            _send(control_ws, control_stats, target_frame),
            _send(churn_ws, churn_stats, target_frame),
        )
        subscribed_at = datetime.now(UTC)
        subscribed_mono = time.monotonic()

        await asyncio.sleep(10)
        initial_control = _snapshot(control_stats, target_assets, time.monotonic())
        initial_churn = _snapshot(churn_stats, target_assets, time.monotonic())
        if not all(count > 0 for count in initial_control["counts"]):
            raise RuntimeError("control target did not receive initial data")
        if not all(count > 0 for count in initial_churn["counts"]):
            raise RuntimeError("churn target did not receive initial data")

        current_five = five
        current_five_assets = list(five_assets)
        stop_at = subscribed_mono + PROBE_SECONDS
        next_ping = subscribed_mono + HEARTBEAT_SECONDS
        next_rotation_check = subscribed_mono + ROTATION_CHECK_SECONDS
        next_snapshot = subscribed_mono + SNAPSHOT_SECONDS

        while time.monotonic() < stop_at:
            now_mono = time.monotonic()
            if control_stats.closed and churn_stats.closed:
                break

            if now_mono >= next_ping:
                if await _send(control_ws, control_stats, "PING"):
                    control_stats.ping_count += 1
                if await _send(churn_ws, churn_stats, "PING"):
                    churn_stats.ping_count += 1
                next_ping += HEARTBEAT_SECONDS

            if now_mono >= next_rotation_check and not churn_stats.closed:
                now_wall = datetime.now(UTC)
                refreshed = await discover_btc_markets(
                    gamma,
                    now_wall,
                    horizons=("5m",),
                    offsets=(-1, 0, 1),
                )
                new_five = _choose_current_5m(refreshed, now_wall)
                if new_five.slug != current_five.slug:
                    new_assets = [new_five.up_token_id, new_five.down_token_id]
                    subscribed = await _send(
                        churn_ws,
                        churn_stats,
                        {"operation": "subscribe", "assets_ids": new_assets},
                    )
                    unsubscribed = await _send(
                        churn_ws,
                        churn_stats,
                        {"operation": "unsubscribe", "assets_ids": current_five_assets},
                    )
                    rotations.append(
                        {
                            "at": now_wall.isoformat(),
                            "target_subscription_age_seconds": round(
                                now_mono - subscribed_mono, 3
                            ),
                            "market_offset_seconds": round(
                                (now_wall - target.window_start_at).total_seconds(), 3
                            ),
                            "from_slug": current_five.slug,
                            "to_slug": new_five.slug,
                            "subscribe_sent": subscribed,
                            "unsubscribe_sent": unsubscribed,
                        }
                    )
                    current_five = new_five
                    current_five_assets = new_assets
                next_rotation_check += ROTATION_CHECK_SECONDS

            if now_mono >= next_snapshot:
                snapshots.append(
                    {
                        "subscription_age_seconds": round(now_mono - subscribed_mono, 3),
                        "market_offset_seconds": round(
                            (datetime.now(UTC) - target.window_start_at).total_seconds(), 3
                        ),
                        "control": _snapshot(control_stats, target_assets, now_mono),
                        "churn": _snapshot(churn_stats, target_assets, now_mono),
                        "rotation_count": len(rotations),
                        "control_pongs": control_stats.pong_count,
                        "churn_pongs": churn_stats.pong_count,
                    }
                )
                next_snapshot += SNAPSHOT_SECONDS

            await asyncio.sleep(0.05)

        finished_mono = time.monotonic()
        final_control = _snapshot(control_stats, target_assets, finished_mono)
        final_churn = _snapshot(churn_stats, target_assets, finished_mono)

        for task in (control_receiver, churn_receiver):
            if not task.done():
                task.cancel()
        await asyncio.gather(control_receiver, churn_receiver, return_exceptions=True)

    def stale(snapshot: dict[str, object]) -> bool:
        ages = snapshot["ages_seconds"]
        return all(age is None or age > 60 for age in ages)

    reproduced = stale(final_churn) and not stale(final_control)
    print(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "mode": "early_target_natural_churn",
                "configured_probe_seconds": PROBE_SECONDS,
                "target_slug": target.slug,
                "target_window_start": target.window_start_at.isoformat(),
                "target_window_end": target.window_end_at.isoformat(),
                "anchor_slug": anchor.slug,
                "anchor_window_end": anchor.window_end_at.isoformat(),
                "target_subscribed_at": subscribed_at.isoformat(),
                "target_subscription_offset_seconds": round(
                    (subscribed_at - target.window_start_at).total_seconds(), 3
                ),
                "subscribe_results": {
                    "control": subscribe_results[0],
                    "churn": subscribe_results[1],
                },
                "initial_control": initial_control,
                "initial_churn": initial_churn,
                "final_control": final_control,
                "final_churn": final_churn,
                "control_close": {
                    "closed": control_stats.closed,
                    "code": control_stats.close_code,
                    "reason": control_stats.close_reason,
                    "at": control_stats.closed_at,
                },
                "churn_close": {
                    "closed": churn_stats.closed,
                    "code": churn_stats.close_code,
                    "reason": churn_stats.close_reason,
                    "at": churn_stats.closed_at,
                },
                "rotation_count": len(rotations),
                "rotations": rotations,
                "early_target_natural_churn_selective_loss_reproduced": reproduced,
                "snapshots": snapshots,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
