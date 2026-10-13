from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from bp_engine.backfill.bybit import BybitHistoryClient, BybitHistoryUnavailableError
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


async def _recent_closed_btc_market_candidates(
    client: GammaClient,
    *,
    now: datetime,
    horizon_minutes: tuple[int, ...] = (5, 15),
    probes_per_horizon: int = 24,
    max_candidates_per_horizon: int = 3,
) -> AsyncIterator[PolymarketMarket]:
    """Yield bounded recent REAL closed markets, never fabricate price points.

    Keep 5m/15m independently represented: an empty first 5m price series
    must not silently count as successful CLOB historical coverage.
    """
    now = _require_aware(now, "now")
    if probes_per_horizon <= 0 or max_candidates_per_horizon <= 0:
        raise ValueError("historical smoke probe and candidate counts must be positive")
    for minutes in horizon_minutes:
        if minutes <= 0:
            raise ValueError("horizon_minutes must contain only positive values")
        seconds = minutes * 60
        aligned_epoch = _floor_epoch(now, seconds)
        found = 0
        # Only completed windows, never a current/just-closed market.
        for offset in range(2, probes_per_horizon + 2):
            slug = f"btc-updown-{minutes}m-{aligned_epoch - offset * seconds}"
            payload = await client.get_market_by_slug(slug)
            if payload is None:
                continue
            market = parse_gamma_market(payload)
            if not market.closed or market.window_end_at > now:
                continue
            yield market
            found += 1
            if found == max_candidates_per_horizon:
                break


async def find_recent_closed_btc_market(
    client: GammaClient,
    *,
    now: datetime,
    horizon_minutes: tuple[int, ...] = (5, 15),
    probes_per_horizon: int = 24,
) -> PolymarketMarket:
    """Preserve the existing first-closed-market lookup for other callers."""
    async for market in _recent_closed_btc_market_candidates(
        client,
        now=now,
        horizon_minutes=horizon_minutes,
        probes_per_horizon=probes_per_horizon,
        max_candidates_per_horizon=1,
    ):
        return market
    raise RuntimeError("no recent closed BTC Up/Down market found for live historical smoke")


async def _find_recent_closed_market_with_both_histories(
    gamma_client: GammaClient,
    price_client: PolymarketPriceHistoryClient,
    *,
    now: datetime,
) -> tuple[PolymarketMarket, int, int, dict[str, int]]:
    """Fail closed unless one RECENT real market has both outcome series.

    Tries at most three closed markets from EACH of the 5m/15m horizons.
    Missing either history is a rejection, not an accepted empty series.
    API/transport/parse errors still propagate; no alternate data provider
    or relaxed fidelity/age requirements are used.
    """
    checked = 0
    empty_up = 0
    empty_down = 0
    checked_horizons = {"5m": 0, "15m": 0}
    async for market in _recent_closed_btc_market_candidates(
        gamma_client, now=now, max_candidates_per_horizon=3
    ):
        checked += 1
        checked_horizons[f"{market.horizon_seconds // 60}m"] += 1
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
        if not up_history.points:
            empty_up += 1
        if not down_history.points:
            empty_down += 1
        if up_history.points and down_history.points:
            return market, len(up_history.points), len(down_history.points), {
                "candidate_markets_checked": checked,
                "empty_up_candidates": empty_up,
                "empty_down_candidates": empty_down,
                "closed_5m_markets_checked": checked_horizons["5m"],
                "closed_15m_markets_checked": checked_horizons["15m"],
            }
    raise RuntimeError(
        "Polymarket historical price smoke could not verify both authentic "
        "outcome histories from a recent closed BTC market "
        f"(candidates_checked={checked}, empty_up={empty_up}, "
        f"empty_down={empty_down}, "
        f"checked_5m={checked_horizons['5m']}, "
        f"checked_15m={checked_horizons['15m']})"
    )


def _count_in_window(candles: tuple[Any, ...], start: datetime, end: datetime) -> int:
    return sum(1 for candle in candles if start <= candle.bucket_at < end)


def _bybit_blocked_report() -> dict[str, Any]:
    return {
        "status": "environment_blocked_http_403",
        "spot_candles": None,
        "linear_candles": None,
    }


async def _bybit_smoke(
    client: BybitHistoryClient,
    *,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    try:
        spot = await client.get_klines(
            category="spot",
            symbol="BTCUSDT",
            interval="1",
            start=start,
            end=end,
            limit=10,
        )
        linear = await client.get_klines(
            category="linear",
            symbol="BTCUSDT",
            interval="1",
            start=start,
            end=end,
            limit=10,
        )
    except BybitHistoryUnavailableError:
        return _bybit_blocked_report()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 403:
            raise
        return _bybit_blocked_report()

    spot_count = _count_in_window(spot.candles, start, end)
    linear_count = _count_in_window(linear.candles, start, end)
    if spot_count == 0:
        raise RuntimeError("Bybit spot historical smoke returned no complete BTC candles")
    if linear_count == 0:
        raise RuntimeError("Bybit linear historical smoke returned no complete BTC candles")
    return {
        "status": "ok",
        "spot_candles": spot_count,
        "linear_candles": linear_count,
    }


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

    market, up_points, down_points, market_probes = (
        await _find_recent_closed_market_with_both_histories(
            gamma_client, price_client, now=checked_at
        )
    )

    btc_end = checked_at.replace(second=0, microsecond=0) - timedelta(minutes=2)
    btc_start = btc_end - timedelta(minutes=3)
    bybit = await _bybit_smoke(bybit_client, start=btc_start, end=btc_end)
    coinbase_spot = await coinbase_client.get_candles(
        product_id="BTC-USD",
        granularity="ONE_MINUTE",
        start=btc_start,
        end=btc_end,
        limit=10,
    )
    coinbase_spot_count = _count_in_window(coinbase_spot.candles, btc_start, btc_end)
    if coinbase_spot_count == 0:
        raise RuntimeError("Coinbase historical smoke returned no complete BTC-USD candles")

    status = "ok" if bybit["status"] == "ok" else "environment_limited"
    return {
        "status": status,
        "checked_at": checked_at.isoformat().replace("+00:00", "Z"),
        "polymarket": {
            "slug": market.slug,
            "horizon_seconds": market.horizon_seconds,
            "resolved_outcome": market.resolved_outcome,
            "up_price_points": up_points,
            "down_price_points": down_points,
            "historical_price_probe": market_probes,
        },
        "btc_window": {
            "start": btc_start.isoformat().replace("+00:00", "Z"),
            "end": btc_end.isoformat().replace("+00:00", "Z"),
        },
        "bybit": bybit,
        "coinbase": {"status": "ok", "spot_candles": coinbase_spot_count},
    }
