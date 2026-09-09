from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
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


def _choose_current(markets: list[object], horizon_seconds: int, now: datetime) -> object:
    candidates = [
        market
        for market in markets
        if market.horizon_seconds == horizon_seconds
        and market.active
        and market.window_start_at <= now <= market.window_end_at
    ]
    if not candidates:
        raise RuntimeError(f"no current {horizon_seconds}s market available")
    return max(candidates, key=lambda market: market.window_start_at)


def _choose_next_15m(markets: list[object], now: datetime) -> object:
    candidates = [
        market
        for market in markets
        if market.horizon_seconds == 900
        and market.active
        and market.window_start_at > now
    ]
    if not candidates:
        raise RuntimeError("no next 15m target market available")
    return min(candidates, key=lambda market: market.window_start_at)


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


async def main() -> None:
    gamma = GammaClient()
    discovered_at = datetime.now(UTC)
    markets = await discover_btc_markets(
        gamma,
        discovered_at,
        horizons=("5m", "15m"),
        offsets=(-1, 0, 1),
    )
    five = _choose_current(markets, 300, discovered_at)
    target = _choose_next_15m(markets, discovered_at)
    five_assets = [five.up_token_id, five.down_token_id]
    target_assets = [target.up_token_id, target.down_token_id]
    target_asset_set = set(target_assets)

    counts: dict[str, int] = defaultdict(int)
    last_seen: dict[str, float] = {}
    rotations: list[dict[str, object]] = []
    snapshots: list[dict[str, object]] = []
    ping_count = 0
    pong_count = 0
    close_code: int | None = None
    close_reason: str | None = None

    async with connect(
        WS_URL,
        ping_interval=None,
        close_timeout=5,
        max_queue=1024,
    ) as websocket:
        await websocket.send(
            json.dumps({"assets_ids": five_assets, "type": "market"}, separators=(",", ":"))
        )
        await websocket.send(
            json.dumps(
                {"operation": "subscribe", "assets_ids": target_assets},
                separators=(",", ":"),
            )
        )
        subscribed_mono = time.monotonic()
        subscribed_at = datetime.now(UTC)
        stop_at = subscribed_mono + PROBE_SECONDS
        next_ping = subscribed_mono + HEARTBEAT_SECONDS
        next_rotation_check = subscribed_mono + ROTATION_CHECK_SECONDS
        next_snapshot = subscribed_mono + SNAPSHOT_SECONDS
        current_five_slug = five.slug
        current_five_assets = list(five_assets)

        while time.monotonic() < stop_at:
            now_mono = time.monotonic()
            if now_mono >= next_ping:
                try:
                    await websocket.send("PING")
                    ping_count += 1
                except ConnectionClosed as exc:
                    close_code = exc.code
                    close_reason = exc.reason
                    break
                next_ping += HEARTBEAT_SECONDS

            if now_mono >= next_rotation_check:
                now_wall = datetime.now(UTC)
                refreshed = await discover_btc_markets(
                    gamma,
                    now_wall,
                    horizons=("5m",),
                    offsets=(-1, 0, 1),
                )
                new_five = _choose_current(refreshed, 300, now_wall)
                if new_five.slug != current_five_slug:
                    new_assets = [new_five.up_token_id, new_five.down_token_id]
                    await websocket.send(
                        json.dumps(
                            {"operation": "subscribe", "assets_ids": new_assets},
                            separators=(",", ":"),
                        )
                    )
                    await websocket.send(
                        json.dumps(
                            {"operation": "unsubscribe", "assets_ids": current_five_assets},
                            separators=(",", ":"),
                        )
                    )
                    rotations.append(
                        {
                            "at": now_wall.isoformat(),
                            "subscription_age_seconds": round(now_mono - subscribed_mono, 3),
                            "from_slug": current_five_slug,
                            "to_slug": new_five.slug,
                        }
                    )
                    current_five_slug = new_five.slug
                    current_five_assets = new_assets
                next_rotation_check += ROTATION_CHECK_SECONDS

            if now_mono >= next_snapshot:
                target_ages = [
                    None if asset not in last_seen else round(now_mono - last_seen[asset], 3)
                    for asset in target_assets
                ]
                snapshots.append(
                    {
                        "subscription_age_seconds": round(now_mono - subscribed_mono, 3),
                        "market_offset_seconds": round(
                            (datetime.now(UTC) - target.window_start_at).total_seconds(), 3
                        ),
                        "target_counts": [counts[asset] for asset in target_assets],
                        "target_ages_seconds": target_ages,
                        "current_five_slug": current_five_slug,
                        "rotation_count": len(rotations),
                        "ping_count": ping_count,
                        "pong_count": pong_count,
                    }
                )
                next_snapshot += SNAPSHOT_SECONDS

            try:
                raw = await asyncio.wait_for(websocket.recv(decode=False), timeout=0.25)
            except TimeoutError:
                continue
            except ConnectionClosed as exc:
                close_code = exc.code
                close_reason = exc.reason
                break

            if raw in {b"PONG", "PONG"}:
                pong_count += 1
                continue
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            try:
                payload = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError:
                continue
            observed = time.monotonic()
            for asset in _asset_ids(payload):
                counts[asset] += 1
                last_seen[asset] = observed

    finished_mono = time.monotonic()
    final_target_ages = {
        asset: None if asset not in last_seen else round(finished_mono - last_seen[asset], 3)
        for asset in target_assets
    }
    print(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "mode": "dynamic_15m_with_narrow_5m_churn",
                "target_slug": target.slug,
                "target_window_start": target.window_start_at.isoformat(),
                "target_window_end": target.window_end_at.isoformat(),
                "target_subscribed_at": subscribed_at.isoformat(),
                "target_subscription_offset_seconds": round(
                    (subscribed_at - target.window_start_at).total_seconds(), 3
                ),
                "subscription_age_seconds": round(finished_mono - subscribed_mono, 3),
                "target_asset_counts": {asset: counts[asset] for asset in target_assets},
                "target_asset_ages_seconds": final_target_ages,
                "target_assets_seen": sum(1 for asset in target_asset_set if counts[asset] > 0),
                "rotation_count": len(rotations),
                "rotations": rotations,
                "ping_count": ping_count,
                "pong_count": pong_count,
                "close_code": close_code,
                "close_reason": close_reason,
                "connection_open_through_probe": close_code is None,
                "snapshots": snapshots,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
