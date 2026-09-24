from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from bp_engine.polymarket.discovery import discover_btc_markets
from bp_engine.polymarket.gamma import GammaClient

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
PROBE_SECONDS = 23 * 60
HEARTBEAT_SECONDS = 10
SNAPSHOT_SECONDS = 60


def _choose_current_5m(markets: list[object], now: datetime) -> object:
    candidates = [
        market
        for market in markets
        if market.horizon_seconds == 300
        and market.active
        and market.window_start_at <= now <= market.window_end_at
    ]
    if not candidates:
        raise RuntimeError("no current 5m bootstrap market available")
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


async def main() -> None:
    discovered_at = datetime.now(UTC)
    markets = await discover_btc_markets(
        GammaClient(),
        discovered_at,
        horizons=("5m", "15m"),
        offsets=(-1, 0, 1),
    )
    bootstrap = _choose_current_5m(markets, discovered_at)
    target = _choose_next_15m(markets, discovered_at)
    bootstrap_assets = [bootstrap.up_token_id, bootstrap.down_token_id]
    target_assets = [target.up_token_id, target.down_token_id]

    target_subscribed_mono: float | None = None
    last_data_mono: float | None = None
    first_data_mono: float | None = None
    data_frames = 0
    pong_count = 0
    ping_count = 0
    snapshots: list[dict[str, object]] = []
    close_code: int | None = None
    close_reason: str | None = None

    async with connect(
        WS_URL,
        ping_interval=None,
        close_timeout=5,
        max_queue=1024,
    ) as websocket:
        await websocket.send(
            json.dumps(
                {"assets_ids": bootstrap_assets, "type": "market"},
                separators=(",", ":"),
            )
        )
        await asyncio.sleep(1)
        await websocket.send(
            json.dumps(
                {"operation": "subscribe", "assets_ids": target_assets},
                separators=(",", ":"),
            )
        )
        target_subscribed_mono = time.monotonic()
        target_subscribed_at = datetime.now(UTC)
        await asyncio.sleep(1)
        await websocket.send(
            json.dumps(
                {"operation": "unsubscribe", "assets_ids": bootstrap_assets},
                separators=(",", ":"),
            )
        )

        next_ping = time.monotonic() + HEARTBEAT_SECONDS
        next_snapshot = target_subscribed_mono + SNAPSHOT_SECONDS

        while time.monotonic() - target_subscribed_mono < PROBE_SECONDS:
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

            if now_mono >= next_snapshot:
                now_wall = datetime.now(UTC)
                age = now_mono - target_subscribed_mono
                last_age = None if last_data_mono is None else now_mono - last_data_mono
                snapshots.append(
                    {
                        "subscription_age_seconds": round(age, 3),
                        "market_offset_seconds": round(
                            (now_wall - target.window_start_at).total_seconds(), 3
                        ),
                        "data_frames": data_frames,
                        "last_data_age_seconds": (
                            None if last_age is None else round(last_age, 3)
                        ),
                        "ping_count": ping_count,
                        "pong_count": pong_count,
                    }
                )
                next_snapshot += SNAPSHOT_SECONDS

            try:
                raw = await asyncio.wait_for(websocket.recv(decode=False), timeout=1.0)
            except TimeoutError:
                continue
            except ConnectionClosed as exc:
                close_code = exc.code
                close_reason = exc.reason
                break

            if raw in {b"PONG", "PONG"}:
                pong_count += 1
                continue
            data_frames += 1
            observed = time.monotonic()
            if first_data_mono is None:
                first_data_mono = observed
            last_data_mono = observed

    finished_mono = time.monotonic()
    finished_at = datetime.now(UTC)
    assert target_subscribed_mono is not None
    final_data_age = (
        None if last_data_mono is None else round(finished_mono - last_data_mono, 3)
    )
    subscription_age = round(finished_mono - target_subscribed_mono, 3)
    market_offset = round((finished_at - target.window_start_at).total_seconds(), 3)

    print(
        json.dumps(
            {
                "generated_at": finished_at.isoformat(),
                "bootstrap_slug": bootstrap.slug,
                "target_slug": target.slug,
                "target_window_start": target.window_start_at.isoformat(),
                "target_window_end": target.window_end_at.isoformat(),
                "target_subscribed_at": target_subscribed_at.isoformat(),
                "target_subscription_offset_seconds": round(
                    (target_subscribed_at - target.window_start_at).total_seconds(), 3
                ),
                "configured_probe_seconds": PROBE_SECONDS,
                "subscription_age_seconds": subscription_age,
                "final_market_offset_seconds": market_offset,
                "data_frames": data_frames,
                "first_data_after_subscription_seconds": (
                    None
                    if first_data_mono is None
                    else round(first_data_mono - target_subscribed_mono, 3)
                ),
                "final_data_age_seconds": final_data_age,
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
