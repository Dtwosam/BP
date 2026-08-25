from __future__ import annotations

import importlib
import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from statistics import pstdev

import pytest

from bp_engine.features.models import FeatureTarget
from bp_engine.features.sources import CandleObservation, PriceObservation, StateObservation


def _calculators():
    return importlib.import_module("bp_engine.features.calculators")


def _target(*, horizon_seconds: int = 300) -> FeatureTarget:
    start = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    return FeatureTarget(
        condition_id="condition-1",
        slug="btc-updown-5m-1787659200",
        horizon_seconds=horizon_seconds,
        market_start_at=start,
        market_end_at=start + timedelta(seconds=horizon_seconds),
    )


def _price(outcome: str, price: str, observed_at: datetime, row_id: int) -> PriceObservation:
    return PriceObservation(
        row_id=row_id,
        source="polymarket_clob_prices_history",
        condition_id="condition-1",
        asset_id=f"{outcome.lower()}-token",
        outcome=outcome,
        observed_at=observed_at,
        price=Decimal(price),
        fidelity_minutes=1,
    )


def _state(state: dict[str, object], *, fresh: bool = True) -> StateObservation:
    when = datetime(2026, 8, 25, 12, 1, 59, tzinfo=UTC)
    return StateObservation(
        row_id=1,
        bucket_at=when,
        state_key="polymarket/market/condition-1/up-token",
        source="polymarket",
        stream="market",
        instrument="condition-1",
        market_id="condition-1",
        asset_id="up-token",
        last_event_at=when,
        state=state,
        fresh=fresh,
        age_seconds=1.0 if fresh else 11.0,
    )


def _candle(index: int, close: Decimal) -> CandleObservation:
    bucket = datetime(2026, 8, 25, 11, 59, tzinfo=UTC) + timedelta(minutes=index)
    return CandleObservation(
        row_id=index + 1,
        source="coinbase",
        market_type="spot",
        symbol="BTC-USD",
        interval_seconds=60,
        bucket_at=bucket,
        open=close - Decimal("0.25"),
        high=close + Decimal("0.50"),
        low=close - Decimal("0.50"),
        close=close,
        volume=Decimal("1") + Decimal(index) / Decimal("10"),
        turnover=None,
    )


def test_time_geometry_for_two_minutes_into_five_minute_market() -> None:
    calculators = _calculators()
    target = _target()
    feature_at = target.market_start_at + timedelta(seconds=120)

    group = calculators.time_geometry(target, feature_at)

    assert group.values["seconds_elapsed"] == 120
    assert group.values["seconds_remaining"] == 180
    assert group.values["fraction_elapsed"] == pytest.approx(0.4)
    assert group.values["horizon_seconds"] == 300
    assert group.missing_flags == {}


def test_polymarket_price_pair_features_and_staleness() -> None:
    calculators = _calculators()
    feature_at = datetime(2026, 8, 25, 12, 2, tzinfo=UTC)
    up = _price("Up", "0.62", feature_at - timedelta(seconds=2), 1)
    down = _price("Down", "0.37", feature_at - timedelta(seconds=3), 2)

    group = calculators.polymarket_prices(up, down, feature_at)

    assert group.values["pm_up_price"] == pytest.approx(0.62)
    assert group.values["pm_down_price"] == pytest.approx(0.37)
    assert group.values["pm_price_sum"] == pytest.approx(0.99)
    assert group.values["pm_up_minus_down"] == pytest.approx(0.25)
    assert group.values["pm_up_price_staleness_s"] == pytest.approx(2.0)
    assert group.values["pm_down_price_staleness_s"] == pytest.approx(3.0)
    assert group.missing_flags["pm_up_price_missing"] is False
    assert group.missing_flags["pm_down_price_missing"] is False


def test_polymarket_pair_features_are_null_when_one_side_is_missing() -> None:
    calculators = _calculators()
    feature_at = datetime(2026, 8, 25, 12, 2, tzinfo=UTC)
    up = _price("Up", "0.62", feature_at, 1)

    group = calculators.polymarket_prices(up, None, feature_at)

    assert group.values["pm_up_price"] == pytest.approx(0.62)
    assert group.values["pm_down_price"] is None
    assert group.values["pm_price_sum"] is None
    assert group.values["pm_up_minus_down"] is None
    assert group.missing_flags["pm_down_price_missing"] is True


def test_book_mid_spread_depth_and_imbalance() -> None:
    calculators = _calculators()
    state = _state(
        {
            "best_bid": "0.60",
            "best_ask": "0.64",
            "bid_depth": "30",
            "ask_depth": "10",
        }
    )

    group = calculators.book_state("pm_up", state)

    assert group.values["pm_up_best_bid"] == pytest.approx(0.60)
    assert group.values["pm_up_best_ask"] == pytest.approx(0.64)
    assert group.values["pm_up_mid"] == pytest.approx(0.62)
    assert group.values["pm_up_spread"] == pytest.approx(0.04)
    assert group.values["pm_up_book_imbalance"] == pytest.approx(0.5)
    assert group.missing_flags["pm_up_book_missing"] is False


def test_book_zero_total_depth_yields_null_imbalance() -> None:
    calculators = _calculators()
    state = _state(
        {
            "best_bid": "0.60",
            "best_ask": "0.64",
            "bid_depth": "0",
            "ask_depth": "0",
        }
    )

    group = calculators.book_state("pm_up", state)

    assert group.values["pm_up_book_imbalance"] is None


def test_stale_book_does_not_emit_current_state_values() -> None:
    calculators = _calculators()
    state = _state({"best_bid": "0.60", "best_ask": "0.64"}, fresh=False)

    group = calculators.book_state("pm_up", state)

    assert group.values["pm_up_best_bid"] is None
    assert group.values["pm_up_best_ask"] is None
    assert group.missing_flags["pm_up_book_missing"] is True
    assert group.missing_flags["pm_up_book_stale"] is True


def test_coinbase_returns_volatility_and_prestart_proxy() -> None:
    calculators = _calculators()
    closes = [Decimal(100 + index) for index in range(17)]
    candles = tuple(_candle(index, close) for index, close in enumerate(closes))
    market_start_at = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)

    group = calculators.coinbase_candles(candles, market_start_at)

    assert group.values["coinbase_latest_close"] == pytest.approx(116.0)
    assert group.values["coinbase_return_1m"] == pytest.approx(116 / 115 - 1)
    assert group.values["coinbase_return_5m"] == pytest.approx(116 / 111 - 1)
    assert group.values["coinbase_return_15m"] == pytest.approx(116 / 101 - 1)
    log_returns = [math.log(float(closes[index] / closes[index - 1])) for index in range(1, 17)]
    assert group.values["coinbase_realized_vol_5m"] == pytest.approx(pstdev(log_returns[-5:]))
    assert group.values["coinbase_realized_vol_15m"] == pytest.approx(pstdev(log_returns[-15:]))
    assert group.values["coinbase_return_from_prestart_close"] == pytest.approx(116 / 100 - 1)
    assert group.missing_flags["coinbase_candles_missing"] is False


def test_insufficient_coinbase_history_is_null_and_flagged_not_zero() -> None:
    calculators = _calculators()
    candles = (_candle(0, Decimal("100")),)
    market_start_at = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)

    group = calculators.coinbase_candles(candles, market_start_at)

    assert group.values["coinbase_latest_close"] == pytest.approx(100.0)
    assert group.values["coinbase_return_1m"] is None
    assert group.values["coinbase_return_5m"] is None
    assert group.values["coinbase_return_15m"] is None
    assert group.values["coinbase_realized_vol_5m"] is None
    assert group.values["coinbase_realized_vol_15m"] is None
    assert group.missing_flags["coinbase_candles_missing"] is True


def test_official_reference_is_explicitly_missing_in_core_v1() -> None:
    calculators = _calculators()

    group = calculators.official_reference()

    assert group.values["official_reference_distance"] is None
    assert group.missing_flags["official_reference_missing"] is True
