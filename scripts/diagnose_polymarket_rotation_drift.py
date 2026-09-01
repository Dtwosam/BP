from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from datetime import UTC, datetime

from websockets.asyncio.client import connect

from bp_engine.polymarket.discovery import discover_btc_markets
from bp_engine.polymarket.gamma import GammaClient

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


async def _discover_probe_markets() -> tuple[object, object, object]:
    now = datetime.now(UTC)
    client = GammaClient()
    markets = await discover_btc_markets(
        client,
        now,
        horizons=("5m", "15m"),
        offsets=(-1, 0, 1),
    )
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
    return fifteen[0], five[-2], five[-1]


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
        for message in messages:
            for asset_id in _asset_ids(message):
                counts[asset_id] += 1
                last_seen[asset_id] = now


async def _run_probe() -> dict[str, object]:
    long_market, five_a, five_b = await _discover_probe_markets()
    long_assets = [long_market.up_token_id, long_market.down_token_id]
    short_a = [five_a.up_token_id, five_a.down_token_id]
    short_b = [five_b.up_token_id, five_b.down_token_id]
    initial_assets = sorted(set(long_assets + short_a))

    counts: dict[str, int] = defaultdict(int)
    last_seen: dict[str, float] = {}
    stop = asyncio.Event()
    started = time.monotonic()

    async with connect(WS_URL, ping_interval=None, close_timeout=5) as websocket:
        await websocket.send(
            json.dumps({"assets_ids": initial_assets, "type": "market"}, separators=(",", ":"))
        )
        heartbeat = asyncio.create_task(_heartbeat(websocket, stop))
        receiver = asyncio.create_task(_receive(websocket, stop, counts, last_seen))
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
                now = time.monotonic()
                snapshots.append(
                    {
                        "cycle": cycle + 1,
                        "long_counts": [counts[asset_id] for asset_id in long_assets],
                        "long_age_seconds": [
                            None
                            if asset_id not in last_seen
                            else round(now - last_seen[asset_id], 3)
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
            now = time.monotonic()
            after_hold = [counts[asset_id] for asset_id in long_assets]
            long_ages = [
                None
                if asset_id not in last_seen
                else round(now - last_seen[asset_id], 3)
                for asset_id in long_assets
            ]
            active_short_counts = [counts[asset_id] for asset_id in active_short]
            reproduced = (
                sum(after_hold) == sum(before_hold)
                and any(count > 0 for count in active_short_counts)
            )
            return {
                "generated_at": datetime.now(UTC).isoformat(),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "long_market_slug": long_market.slug,
                "five_market_a_slug": five_a.slug,
                "five_market_b_slug": five_b.slug,
                "initial_long_counts": snapshots[0]["long_counts"] if snapshots else [],
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


async def main() -> None:
    result = await _run_probe()
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
