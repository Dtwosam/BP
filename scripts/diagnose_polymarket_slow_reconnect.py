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
SLOW_PHASE_SECONDS = 90
POST_RECONNECT_SECONDS = 45
SLOW_PROCESSING_SECONDS = 0.01
HEARTBEAT_SECONDS = 10


def _asset_ids(message: object) -> set[str]:
    if not isinstance(message, dict):
        return set()
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


def _decode_assets(raw: object) -> set[str]:
    if raw == "PONG":
        return set()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return set()
    messages = payload if isinstance(payload, list) else [payload]
    observed: set[str] = set()
    for message in messages:
        observed.update(_asset_ids(message))
    return observed


async def _slow_phase(assets: list[str]) -> dict[str, object]:
    started = time.monotonic()
    next_heartbeat = started + HEARTBEAT_SECONDS
    message_count = 0
    counts: dict[str, int] = defaultdict(int)
    close_code: int | None = None
    close_reason: str | None = None

    async with connect(WS_URL, ping_interval=None, close_timeout=5, max_queue=16) as websocket:
        await websocket.send(
            json.dumps({"assets_ids": assets, "type": "market"}, separators=(",", ":"))
        )
        while time.monotonic() - started < SLOW_PHASE_SECONDS:
            now_mono = time.monotonic()
            if now_mono >= next_heartbeat:
                try:
                    await websocket.send("PING")
                except ConnectionClosed as exc:
                    close_code = exc.code
                    close_reason = exc.reason
                    break
                next_heartbeat += HEARTBEAT_SECONDS
            try:
                raw = await asyncio.wait_for(websocket.recv(), timeout=1.0)
            except TimeoutError:
                continue
            except ConnectionClosed as exc:
                close_code = exc.code
                close_reason = exc.reason
                break
            if raw == "PONG":
                continue
            message_count += 1
            for asset_id in _decode_assets(raw):
                counts[asset_id] += 1
            await asyncio.sleep(SLOW_PROCESSING_SECONDS)

    return {
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "message_count": message_count,
        "asset_counts": {asset_id: counts[asset_id] for asset_id in assets},
        "close_code": close_code,
        "close_reason": close_reason,
        "slow_consumer_close": close_code == 1013 and "slow consumer" in (close_reason or ""),
    }


async def _fast_reconnect_phase(assets: list[str]) -> dict[str, object]:
    started = time.monotonic()
    next_heartbeat = started + HEARTBEAT_SECONDS
    counts: dict[str, int] = defaultdict(int)
    last_seen: dict[str, float] = {}
    message_count = 0
    close_code: int | None = None
    close_reason: str | None = None

    async with connect(WS_URL, ping_interval=None, close_timeout=5, max_queue=16) as websocket:
        await websocket.send(
            json.dumps({"assets_ids": assets, "type": "market"}, separators=(",", ":"))
        )
        while time.monotonic() - started < POST_RECONNECT_SECONDS:
            now_mono = time.monotonic()
            if now_mono >= next_heartbeat:
                try:
                    await websocket.send("PING")
                except ConnectionClosed as exc:
                    close_code = exc.code
                    close_reason = exc.reason
                    break
                next_heartbeat += HEARTBEAT_SECONDS
            try:
                raw = await asyncio.wait_for(websocket.recv(), timeout=1.0)
            except TimeoutError:
                continue
            except ConnectionClosed as exc:
                close_code = exc.code
                close_reason = exc.reason
                break
            if raw == "PONG":
                continue
            message_count += 1
            observed_at = time.monotonic()
            for asset_id in _decode_assets(raw):
                counts[asset_id] += 1
                last_seen[asset_id] = observed_at

    finished = time.monotonic()
    return {
        "elapsed_seconds": round(finished - started, 3),
        "message_count": message_count,
        "asset_counts": {asset_id: counts[asset_id] for asset_id in assets},
        "asset_ages_seconds": {
            asset_id: None
            if asset_id not in last_seen
            else round(finished - last_seen[asset_id], 3)
            for asset_id in assets
        },
        "assets_with_events": sum(1 for asset_id in assets if counts[asset_id] > 0),
        "close_code": close_code,
        "close_reason": close_reason,
    }


async def main() -> None:
    now = datetime.now(UTC)
    markets = await discover_btc_markets(
        GammaClient(),
        now,
        horizons=("5m", "15m"),
        offsets=(-1, 0, 1),
    )
    five = _choose_current(markets, 300, now)
    fifteen = _choose_current(markets, 900, now)
    five_assets = [five.up_token_id, five.down_token_id]
    fifteen_assets = [fifteen.up_token_id, fifteen.down_token_id]
    assets = sorted(set(five_assets + fifteen_assets))

    slow = await _slow_phase(assets)
    reconnect_started_at = datetime.now(UTC)
    fast = await _fast_reconnect_phase(assets)

    print(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "five_market_slug": five.slug,
                "five_window_end": five.window_end_at.isoformat(),
                "fifteen_market_slug": fifteen.slug,
                "fifteen_window_end": fifteen.window_end_at.isoformat(),
                "asset_count": len(assets),
                "five_assets": five_assets,
                "fifteen_assets": fifteen_assets,
                "slow_phase": slow,
                "reconnect_started_at": reconnect_started_at.isoformat(),
                "post_reconnect": fast,
                "all_expected_assets_resumed": fast["assets_with_events"] == len(assets),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
