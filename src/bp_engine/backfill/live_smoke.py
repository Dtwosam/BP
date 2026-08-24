from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from bp_engine.backfill.bybit import BybitHistoryClient
from bp_engine.backfill.coinbase import CoinbaseHistoryClient
from bp_engine.backfill.polymarket_prices import PolymarketPriceHistoryClient
from bp_engine.polymarket.gamma import GammaClient
from bp_engine.polymarket.models import PolymarketMarket
from bp_engine.polymarket.parsing import parse_gamma_market


def _require_aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _floor_epoch(value: datetime, seconds: int) -> int:
    epoch = int(value.timestamp())
    return epoch - (epoch % seconds)


async def find_recent_closed_btc_market(
    client: GammaClient,
    *,
    now: datetime,
    horizon_minutes: tuple[int, ...] = (5, 15),
    probes_per_horizon: int = 24,
) -> PolymarketMarket:
    now = _require_aware(now, "now")
    if probes_per_horizon <= 0:
        raise ValueError("probes_per_horizon must be positive")

    for minutes in horizon_minutes:
        if minutes <= 0:
            raise ValueError("horizon_minutes must contain only positive values")
        seconds = minutes * 60
        aligned_epoch = _floor_epoch(now, seconds)
        # Start two windows behind the current aligned boundary. This avoids probing an
        # in-flight or just-ended market while keeping the smoke check recent.
        for offset in range(2, probes_per_horizon + 2):
            start_epoch = aligned_epoch - offset * seconds
            slug = f"btc-updown-{minutes}m-{start_epoch}"
            payload = await client.get_market_by_slug(slug)
            if payload is None:
                continue
            market = parse_gamma_market(payload)
            if market.closed and market.window_end_at <= now:
                return market

    raise RuntimeError("no recent closed BTC Up/Down market found for live historical smoke")


def _count_in_window(candles: tuple[Any, ...], start: datetime, end: datetime) -> int:
    return sum(1 for candle in candles if start <= candle.bucket_at < end)


async def run_live_source_smoke(
    *,
    now: datetime | None = None,
    gamma_client: GammaClient | None = None,
    price_client: PolymarketPriceHistoryClient | None = None,
    bybit_client: BybitHistoryClient | None = None,
    coinbase_client: CoinbaseHistoryClient | None = None,
) -> dict[str, Any]:
    checked_at = _require_aware(now or datetime.now(UTC), "now")
    gamma_client = gamma_client or GammaClient()
    price_client = price_client or PolymarketPriceHistoryClient()
    bybit_client = bybit_client or BybitHistoryClient()
    coinbase_client = coinbase_client or CoinbaseHistoryClient()

    market = await find_recent_closed_btc_market(gamma_client, now=checked_at)
    up_history = await price_client.get_history(
        market.up_token_id,
        start=market.window_start_at,
        end=market.window_end_at,
        fidelity_minutes=1,
    )
    down_history = await price_client.get_history(
        market.down_token_id,
        start=market.window_start_at,
        end=market.window_end_at,
        fidelity_minutes=1,
    )
    if not up_history.points or not down_history.points:
        raise RuntimeError("Polymarket historical price smoke returned an empty outcome series")

    btc_end = checked_at.replace(second=0, microsecond=0) - timedelta(minutes=2)
    btc_start = btc_end - timedelta(minutes=3)
    bybit_spot = await bybit_client.get_klines(
        category="spot",
        symbol="BTCUSDT",
        interval="1",
        start=btc_start,
        end=btc_end,
        limit=10,
    )
    bybit_linear = await bybit_client.get_klines(
        category="linear",
        symbol="BTCUSDT",
        interval="1",
        start=btc_start,
        end=btc_end,
        limit=10,
    )
    coinbase_spot = await coinbase_client.get_candles(
        product_id="BTC-USD",
        granularity="ONE_MINUTE",
        start=btc_start,
        end=btc_end,
        limit=10,
    )

    bybit_spot_count = _count_in_window(bybit_spot.candles, btc_start, btc_end)
    bybit_linear_count = _count_in_window(bybit_linear.candles, btc_start, btc_end)
    coinbase_spot_count = _count_in_window(coinbase_spot.candles, btc_start, btc_end)
    if bybit_spot_count == 0:
        raise RuntimeError("Bybit spot historical smoke returned no complete BTC candles")
    if bybit_linear_count == 0:
        raise RuntimeError("Bybit linear historical smoke returned no complete BTC candles")
    if coinbase_spot_count == 0:
        raise RuntimeError("Coinbase historical smoke returned no complete BTC-USD candles")

    return {
        "status": "ok",
        "checked_at": checked_at.isoformat().replace("+00:00", "Z"),
        "polymarket": {
            "slug": market.slug,
            "horizon_seconds": market.horizon_seconds,
            "resolved_outcome": market.resolved_outcome,
            "up_price_points": len(up_history.points),
            "down_price_points": len(down_history.points),
        },
        "btc_window": {
            "start": btc_start.isoformat().replace("+00:00", "Z"),
            "end": btc_end.isoformat().replace("+00:00", "Z"),
        },
        "bybit": {
            "spot_candles": bybit_spot_count,
            "linear_candles": bybit_linear_count,
        },
        "coinbase": {"spot_candles": coinbase_spot_count},
    }
