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
CYCLES = 12
SUBSCRIBE_HOLD_SECONDS = 2
POST_UNSUBSCRIBE_SECONDS = 3
HEARTBEAT_SECONDS = 10


@dataclass
class Stats:
    counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    last_seen: dict[str, float] = field(default_factory=dict)
    pong_count: int = 0
    closed: bool = False
    close_code: int | None = None
    close_reason: str | None = None


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


def _choose_five(markets: list[object], now: datetime) -> tuple[object, object]:
    five = sorted(
        [market for market in markets if market.horizon_seconds == 300 and market.active],
        key=lambda market: market.window_start_at,
    )
    current = [market for market in five if market.window_start_at <= now <= market.window_end_at]
    future = [market for market in five if market.window_start_at > now]
    if not current or not future:
        raise RuntimeError("need current and future 5m markets for churn diagnostic")
    return current[-1], future[0]


def _choose_target(markets: list[object], now: datetime) -> object:
    candidates = [
        market
        for market in markets
        if market.horizon_seconds == 900 and market.active and market.window_start_at > now
    ]
    if not candidates:
        raise RuntimeError("no future 15m target available")
    return min(candidates, key=lambda market: market.window_start_at)


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


async def _send(websocket: object, stats: Stats, payload: object) -> bool:
    if stats.closed:
        return False
    try:
        await websocket.send(payload if isinstance(payload, str) else _wire(payload))
    except ConnectionClosed as exc:
        stats.closed = True
        stats.close_code = exc.code
        stats.close_reason = exc.reason
        return False
    return True


def _target_snapshot(stats: Stats, target_assets: list[str], now_mono: float) -> dict[str, object]:
    ages = [
        None if asset not in stats.last_seen else round(now_mono - stats.last_seen[asset], 3)
        for asset in target_assets
    ]
    return {
        "counts": [stats.counts[asset] for asset in target_assets],
        "ages_seconds": ages,
        "closed": stats.closed,
    }


async def main() -> None:
    discovered_at = datetime.now(UTC)
    markets = await discover_btc_markets(
        GammaClient(),
        discovered_at,
        horizons=("5m", "15m"),
        offsets=(-1, 0, 1),
    )
    five_a, five_b = _choose_five(markets, discovered_at)
    target = _choose_target(markets, discovered_at)
    short_a = [five_a.up_token_id, five_a.down_token_id]
    short_b = [five_b.up_token_id, five_b.down_token_id]
    target_assets = [target.up_token_id, target.down_token_id]

    control_stats = Stats()
    churn_stats = Stats()
    cycle_snapshots: list[dict[str, object]] = []

    async with AsyncExitStack() as stack:
        control_ws = await stack.enter_async_context(
            connect(WS_URL, ping_interval=None, close_timeout=5, max_queue=1024)
        )
        churn_ws = await stack.enter_async_context(
            connect(WS_URL, ping_interval=None, close_timeout=5, max_queue=1024)
        )
        await control_ws.send(_wire({"assets_ids": short_a, "type": "market"}))
        await churn_ws.send(_wire({"assets_ids": short_a, "type": "market"}))
        control_receiver = asyncio.create_task(_receive(control_ws, control_stats))
        churn_receiver = asyncio.create_task(_receive(churn_ws, churn_stats))

        target_frame = {"operation": "subscribe", "assets_ids": target_assets}
        await asyncio.gather(
            _send(control_ws, control_stats, target_frame),
            _send(churn_ws, churn_stats, target_frame),
        )
        target_subscribed_at = datetime.now(UTC)
        target_subscribed_mono = time.monotonic()

        await asyncio.sleep(8)
        initial_control = _target_snapshot(control_stats, target_assets, time.monotonic())
        initial_churn = _target_snapshot(churn_stats, target_assets, time.monotonic())
        if not all(count > 0 for count in initial_control["counts"]):
            raise RuntimeError("control target did not receive initial data")
        if not all(count > 0 for count in initial_churn["counts"]):
            raise RuntimeError("churn target did not receive initial data")

        active = short_a
        alternate = short_b
        last_ping = time.monotonic()
        for cycle in range(1, CYCLES + 1):
            await _send(
                churn_ws,
                churn_stats,
                {"operation": "subscribe", "assets_ids": alternate},
            )
            await asyncio.sleep(SUBSCRIBE_HOLD_SECONDS)
            await _send(
                churn_ws,
                churn_stats,
                {"operation": "unsubscribe", "assets_ids": active},
            )
            await asyncio.sleep(POST_UNSUBSCRIBE_SECONDS)

            now_mono = time.monotonic()
            if now_mono - last_ping >= HEARTBEAT_SECONDS:
                await asyncio.gather(
                    _send(control_ws, control_stats, "PING"),
                    _send(churn_ws, churn_stats, "PING"),
                )
                last_ping = now_mono

            cycle_snapshots.append(
                {
                    "cycle": cycle,
                    "elapsed_since_target_seconds": round(
                        now_mono - target_subscribed_mono, 3
                    ),
                    "control": _target_snapshot(control_stats, target_assets, now_mono),
                    "churn": _target_snapshot(churn_stats, target_assets, now_mono),
                }
            )
            active, alternate = alternate, active

        await asyncio.sleep(20)
        finished_mono = time.monotonic()
        final_control = _target_snapshot(control_stats, target_assets, finished_mono)
        final_churn = _target_snapshot(churn_stats, target_assets, finished_mono)

        for task in (control_receiver, churn_receiver):
            if not task.done():
                task.cancel()
        await asyncio.gather(control_receiver, churn_receiver, return_exceptions=True)

    def stale(snapshot: dict[str, object]) -> bool:
        ages = snapshot["ages_seconds"]
        return all(age is None or age > 10 for age in ages)

    reproduced = stale(final_churn) and not stale(final_control)
    print(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "mode": "dynamic_target_post_add_churn",
                "target_slug": target.slug,
                "target_window_start": target.window_start_at.isoformat(),
                "target_subscribed_at": target_subscribed_at.isoformat(),
                "target_subscription_offset_seconds": round(
                    (target_subscribed_at - target.window_start_at).total_seconds(), 3
                ),
                "five_a_slug": five_a.slug,
                "five_b_slug": five_b.slug,
                "cycle_count": CYCLES,
                "initial_control": initial_control,
                "initial_churn": initial_churn,
                "final_control": final_control,
                "final_churn": final_churn,
                "control_close": {
                    "closed": control_stats.closed,
                    "code": control_stats.close_code,
                    "reason": control_stats.close_reason,
                },
                "churn_close": {
                    "closed": churn_stats.closed,
                    "code": churn_stats.close_code,
                    "reason": churn_stats.close_reason,
                },
                "post_add_churn_selective_loss_reproduced": reproduced,
                "cycles": cycle_snapshots,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
