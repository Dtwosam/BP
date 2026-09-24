from __future__ import annotations

import asyncio
import json
import os
import time
from collections import defaultdict
from datetime import UTC, datetime

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from bp_engine.polymarket.discovery import discover_btc_markets
from bp_engine.polymarket.gamma import GammaClient

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
PROBE_SECONDS = 5 * 60
HEARTBEAT_SECONDS = 10
SNAPSHOT_SECONDS = 60


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


def _choose_current(markets: list[object], horizon_seconds: int, now: datetime) -> object:
    candidates = [
        market
        for market in markets
        if market.horizon_seconds == horizon_seconds
        and market.active
        and market.window_start_at <= now <= market.window_end_at
    ]
    if not candidates:
        candidates = [
            market
            for market in markets
            if market.horizon_seconds == horizon_seconds and market.active
        ]
    if not candidates:
        raise RuntimeError(f"no active {horizon_seconds}s market available")
    return max(candidates, key=lambda market: market.window_start_at)


def _summary(
    asset_ids: list[str],
    counts: dict[str, int],
    last_seen: dict[str, float],
    now_mono: float,
) -> dict[str, object]:
    ages = [
        None if asset_id not in last_seen else round(now_mono - last_seen[asset_id], 3)
        for asset_id in asset_ids
    ]
    return {
        "asset_count": len(asset_ids),
        "event_count": sum(counts[asset_id] for asset_id in asset_ids),
        "assets_with_events": sum(1 for asset_id in asset_ids if counts[asset_id] > 0),
        "max_age_seconds": max((age for age in ages if age is not None), default=None),
        "ages_seconds": ages,
    }


async def _run_probe() -> dict[str, object]:
    configured_max_queue = int(os.environ.get("POLYMARKET_MAX_QUEUE", "16"))
    if configured_max_queue <= 0:
        raise ValueError("POLYMARKET_MAX_QUEUE must be greater than zero")

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

    counts: dict[str, int] = defaultdict(int)
    last_seen: dict[str, float] = {}
    message_count = 0
    total_asset_observations = 0
    snapshots: list[dict[str, object]] = []
    started = time.monotonic()
    next_heartbeat = started + HEARTBEAT_SECONDS
    next_snapshot = started + SNAPSHOT_SECONDS
    close_code: int | None = None
    close_reason: str | None = None
    close_elapsed_seconds: float | None = None

    async with connect(
        WS_URL,
        ping_interval=None,
        close_timeout=5,
        max_queue=configured_max_queue,
    ) as websocket:
        await websocket.send(
            json.dumps({"assets_ids": assets, "type": "market"}, separators=(",", ":"))
        )

        while True:
            now_mono = time.monotonic()
            elapsed = now_mono - started
            if elapsed >= PROBE_SECONDS:
                break

            if now_mono >= next_heartbeat:
                try:
                    await websocket.send("PING")
                except ConnectionClosed as exc:
                    close_code = exc.code
                    close_reason = exc.reason
                    close_elapsed_seconds = round(elapsed, 3)
                    break
                next_heartbeat += HEARTBEAT_SECONDS

            if now_mono >= next_snapshot:
                snapshots.append(
                    {
                        "elapsed_seconds": round(elapsed, 3),
                        "message_count": message_count,
                        "five_minute": _summary(
                            five_assets, counts, last_seen, now_mono
                        ),
                        "fifteen_minute": _summary(
                            fifteen_assets, counts, last_seen, now_mono
                        ),
                    }
                )
                next_snapshot += SNAPSHOT_SECONDS

            try:
                raw = await asyncio.wait_for(websocket.recv(), timeout=1.0)
            except TimeoutError:
                continue
            except ConnectionClosed as exc:
                close_code = exc.code
                close_reason = exc.reason
                close_elapsed_seconds = round(time.monotonic() - started, 3)
                break

            if raw == "PONG":
                continue
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            try:
                payload = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError:
                continue

            message_count += 1
            messages = payload if isinstance(payload, list) else [payload]
            observed_at = time.monotonic()
            for message in messages:
                observed_assets = _asset_ids(message)
                total_asset_observations += len(observed_assets)
                for asset_id in observed_assets:
                    counts[asset_id] += 1
                    last_seen[asset_id] = observed_at

    finished = time.monotonic()
    return {
        "mode": "static_narrow",
        "configured_max_queue": configured_max_queue,
        "generated_at": datetime.now(UTC).isoformat(),
        "configured_probe_seconds": PROBE_SECONDS,
        "elapsed_seconds": round(finished - started, 3),
        "subscription_asset_count": len(assets),
        "five_market_slug": five.slug,
        "fifteen_market_slug": fifteen.slug,
        "message_count": message_count,
        "total_asset_observations": total_asset_observations,
        "close_code": close_code,
        "close_reason": close_reason,
        "close_elapsed_seconds": close_elapsed_seconds,
        "server_closed_early": close_code is not None,
        "five_minute": _summary(five_assets, counts, last_seen, finished),
        "fifteen_minute": _summary(fifteen_assets, counts, last_seen, finished),
        "snapshots": snapshots,
    }


async def main() -> None:
    print(json.dumps(await _run_probe(), sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
