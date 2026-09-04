from __future__ import annotations

import asyncio
import json
import os
import time
from collections import defaultdict
from datetime import UTC, datetime

from websockets.asyncio.client import connect

from bp_engine.polymarket.discovery import discover_btc_markets
from bp_engine.polymarket.gamma import GammaClient
from bp_engine.recorder.polymarket_coordinator import PolymarketSubscriptionCoordinator

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


def _asset_ids(message: object) -> set[str]:
    if not isinstance(message, dict):
        return set()
    assets: set[str] = set()
    asset_id = message.get("asset_id")
    if asset_id:
        assets.add(str(asset_id))
    price_changes = message.get("price_changes")
    if isinstance(price_changes, list):
        for change in price_changes:
            if isinstance(change, dict) and change.get("asset_id"):
                assets.add(str(change["asset_id"]))
    return assets


async def _heartbeat(websocket: object, stop: asyncio.Event) -> None:
    while not stop.is_set():
        await asyncio.sleep(10)
        if stop.is_set():
            return
        await websocket.send("PING")


async def _receive(
    websocket: object,
    stop: asyncio.Event,
    counts: dict[str, int],
    last_seen: dict[str, float],
    global_last_seen: list[float],
) -> None:
    while not stop.is_set():
        raw = await websocket.recv()
        if raw == "PONG":
            continue
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            continue
        messages = payload if isinstance(payload, list) else [payload]
        now = time.monotonic()
        global_last_seen[0] = now
        for message in messages:
            for asset_id in _asset_ids(message):
                counts[asset_id] += 1
                last_seen[asset_id] = now


async def _discover(client: GammaClient, now: datetime) -> list[object]:
    return await discover_btc_markets(
        client,
        now,
        horizons=("5m", "15m"),
        offsets=(-1, 0, 1),
    )


async def _run_accelerated_probe() -> dict[str, object]:
    now = datetime.now(UTC)
    client = GammaClient()
    markets = await _discover(client, now)
    five = sorted(
        [market for market in markets if market.horizon_seconds == 300 and market.active],
        key=lambda market: market.window_start_at,
    )
    fifteen = sorted(
        [market for market in markets if market.horizon_seconds == 900 and market.active],
        key=lambda market: market.window_end_at,
        reverse=True,
    )
    if len(five) < 2 or not fifteen:
        raise RuntimeError("insufficient active BTC markets for rotation diagnostic")

    long_market = fifteen[0]
    five_a, five_b = five[-2], five[-1]
    long_assets = [long_market.up_token_id, long_market.down_token_id]
    short_a = [five_a.up_token_id, five_a.down_token_id]
    short_b = [five_b.up_token_id, five_b.down_token_id]
    initial_assets = sorted(set(long_assets + short_a))

    counts: dict[str, int] = defaultdict(int)
    last_seen: dict[str, float] = {}
    global_last_seen = [time.monotonic()]
    stop = asyncio.Event()
    started = time.monotonic()

    async with connect(WS_URL, ping_interval=None, close_timeout=5) as websocket:
        await websocket.send(
            json.dumps({"assets_ids": initial_assets, "type": "market"}, separators=(",", ":"))
        )
        heartbeat = asyncio.create_task(_heartbeat(websocket, stop))
        receiver = asyncio.create_task(
            _receive(websocket, stop, counts, last_seen, global_last_seen)
        )
        try:
            await asyncio.sleep(10)
            snapshots: list[dict[str, object]] = []
            active_short = short_a
            alternate_short = short_b
            for cycle in range(6):
                await websocket.send(
                    json.dumps(
                        {"operation": "subscribe", "assets_ids": alternate_short},
                        separators=(",", ":"),
                    )
                )
                await asyncio.sleep(2)
                await websocket.send(
                    json.dumps(
                        {"operation": "unsubscribe", "assets_ids": active_short},
                        separators=(",", ":"),
                    )
                )
                await asyncio.sleep(3)
                now_mono = time.monotonic()
                snapshots.append(
                    {
                        "cycle": cycle + 1,
                        "long_counts": [counts[asset_id] for asset_id in long_assets],
                        "long_age_seconds": [
                            None
                            if asset_id not in last_seen
                            else round(now_mono - last_seen[asset_id], 3)
                            for asset_id in long_assets
                        ],
                        "active_short_counts": [
                            counts[asset_id] for asset_id in alternate_short
                        ],
                    }
                )
                active_short, alternate_short = alternate_short, active_short

            before_hold = [counts[asset_id] for asset_id in long_assets]
            await asyncio.sleep(30)
            now_mono = time.monotonic()
            after_hold = [counts[asset_id] for asset_id in long_assets]
            long_ages = [
                None
                if asset_id not in last_seen
                else round(now_mono - last_seen[asset_id], 3)
                for asset_id in long_assets
            ]
            active_short_counts = [counts[asset_id] for asset_id in active_short]
            reproduced = (
                sum(after_hold) == sum(before_hold)
                and any(count > 0 for count in active_short_counts)
            )
            return {
                "mode": "accelerated",
                "generated_at": datetime.now(UTC).isoformat(),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "long_market_slug": long_market.slug,
                "five_market_a_slug": five_a.slug,
                "five_market_b_slug": five_b.slug,
                "snapshots": snapshots,
                "before_hold_long_counts": before_hold,
                "after_hold_long_counts": after_hold,
                "after_hold_long_age_seconds": long_ages,
                "after_hold_active_short_counts": active_short_counts,
                "accelerated_rotation_drift_reproduced": reproduced,
            }
        finally:
            stop.set()
            heartbeat.cancel()
            receiver.cancel()
            await asyncio.gather(heartbeat, receiver, return_exceptions=True)


async def _run_natural_probe() -> dict[str, object]:
    started_at = datetime.now(UTC)
    started = time.monotonic()
    client = GammaClient()

    async def discovery(now: datetime) -> list[object]:
        return await _discover(client, now)

    markets = await discovery(started_at)
    target_candidates = sorted(
        [
            market
            for market in markets
            if market.horizon_seconds == 900
            and market.active
            and market.window_start_at > started_at
        ],
        key=lambda market: market.window_start_at,
    )
    if not target_candidates:
        raise RuntimeError("no next active 15m BTC market available")
    target = target_candidates[0]
    if (target.window_end_at - started_at).total_seconds() < 23 * 60:
        raise RuntimeError("next 15m market does not remain open long enough for probe")

    target_assets = frozenset({target.up_token_id, target.down_token_id})
    coordinator = PolymarketSubscriptionCoordinator(discovery, grace_seconds=30)
    initial = await coordinator.refresh(started_at)
    initial_assets = sorted(initial.current - target_assets)
    if not initial_assets:
        raise RuntimeError("empty initial non-target subscription set")

    counts: dict[str, int] = defaultdict(int)
    last_seen: dict[str, float] = {}
    global_last_seen = [started]
    stop = asyncio.Event()
    wire_updates: list[dict[str, object]] = []
    snapshots: list[dict[str, object]] = []

    async with connect(WS_URL, ping_interval=None, close_timeout=5) as websocket:
        await websocket.send(
            json.dumps({"assets_ids": initial_assets, "type": "market"}, separators=(",", ":"))
        )
        heartbeat = asyncio.create_task(_heartbeat(websocket, stop))
        receiver = asyncio.create_task(
            _receive(websocket, stop, counts, last_seen, global_last_seen)
        )
        try:
            await asyncio.sleep(2)
            await websocket.send(
                json.dumps(
                    {"operation": "subscribe", "assets_ids": sorted(target_assets)},
                    separators=(",", ":"),
                )
            )
            target_subscribed_at = time.monotonic()
            wire_updates.append(
                {
                    "elapsed_seconds": round(target_subscribed_at - started, 3),
                    "operation": "subscribe",
                    "asset_count": len(target_assets),
                    "contains_target": True,
                }
            )

            await asyncio.sleep(15)
            initial_target_counts = [counts[asset_id] for asset_id in target_assets]
            if not all(count > 0 for count in initial_target_counts):
                raise RuntimeError("target 15m pair did not deliver initial websocket data")

            probe_seconds = 22 * 60
            next_snapshot = 5 * 60
            while time.monotonic() - target_subscribed_at < probe_seconds:
                await asyncio.sleep(30)
                now = datetime.now(UTC)
                diff = await coordinator.refresh(now)
                if diff.added:
                    payload = sorted(diff.added)
                    await websocket.send(
                        json.dumps(
                            {"operation": "subscribe", "assets_ids": payload},
                            separators=(",", ":"),
                        )
                    )
                    wire_updates.append(
                        {
                            "elapsed_seconds": round(
                                time.monotonic() - target_subscribed_at, 3
                            ),
                            "operation": "subscribe",
                            "asset_count": len(payload),
                            "contains_target": bool(target_assets.intersection(payload)),
                        }
                    )
                if diff.removed:
                    payload = sorted(diff.removed)
                    await websocket.send(
                        json.dumps(
                            {"operation": "unsubscribe", "assets_ids": payload},
                            separators=(",", ":"),
                        )
                    )
                    wire_updates.append(
                        {
                            "elapsed_seconds": round(
                                time.monotonic() - target_subscribed_at, 3
                            ),
                            "operation": "unsubscribe",
                            "asset_count": len(payload),
                            "contains_target": bool(target_assets.intersection(payload)),
                        }
                    )

                elapsed = time.monotonic() - target_subscribed_at
                if elapsed >= next_snapshot:
                    now_mono = time.monotonic()
                    target_ages = [
                        None
                        if asset_id not in last_seen
                        else round(now_mono - last_seen[asset_id], 3)
                        for asset_id in target_assets
                    ]
                    snapshots.append(
                        {
                            "elapsed_seconds": round(elapsed, 3),
                            "target_counts": [counts[asset_id] for asset_id in target_assets],
                            "target_age_seconds": target_ages,
                            "stream_age_seconds": round(now_mono - global_last_seen[0], 3),
                            "desired_asset_count": len(diff.current),
                        }
                    )
                    next_snapshot += 5 * 60

            now_mono = time.monotonic()
            target_ages = [
                None
                if asset_id not in last_seen
                else round(now_mono - last_seen[asset_id], 3)
                for asset_id in target_assets
            ]
            stream_age = round(now_mono - global_last_seen[0], 3)
            target_stale = all(age is None or age > 60 for age in target_ages)
            stream_healthy = stream_age < 10
            target_removed_by_client = any(
                update["operation"] == "unsubscribe" and update["contains_target"]
                for update in wire_updates
            )
            return {
                "mode": "natural",
                "generated_at": datetime.now(UTC).isoformat(),
                "target_market_slug": target.slug,
                "target_window_start_at": target.window_start_at.isoformat(),
                "target_window_end_at": target.window_end_at.isoformat(),
                "target_subscribed_at": (
                    started_at
                    + (datetime.now(UTC) - datetime.now(UTC))
                ).isoformat(),
                "elapsed_since_target_subscribe_seconds": round(
                    time.monotonic() - target_subscribed_at, 3
                ),
                "initial_target_counts": initial_target_counts,
                "final_target_counts": [counts[asset_id] for asset_id in target_assets],
                "final_target_age_seconds": target_ages,
                "final_stream_age_seconds": stream_age,
                "target_removed_by_client": target_removed_by_client,
                "wire_update_count": len(wire_updates),
                "wire_updates": wire_updates,
                "snapshots": snapshots,
                "natural_rotation_drift_reproduced": target_stale and stream_healthy,
            }
        finally:
            stop.set()
            heartbeat.cancel()
            receiver.cancel()
            await asyncio.gather(heartbeat, receiver, return_exceptions=True)


async def main() -> None:
    mode = os.environ.get("POLYMARKET_ROTATION_DIAGNOSTIC_MODE", "accelerated")
    if mode == "accelerated":
        result = await _run_accelerated_probe()
    elif mode == "natural":
        result = await _run_natural_probe()
    else:
        raise ValueError(f"unsupported diagnostic mode: {mode}")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
