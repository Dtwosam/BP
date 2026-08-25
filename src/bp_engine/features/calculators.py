from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from statistics import pstdev
from typing import Any

from bp_engine.features.models import FeatureTarget
from bp_engine.features.sources import CandleObservation, PriceObservation, StateObservation


@dataclass(frozen=True)
class FeatureGroup:
    values: dict[str, Any]
    missing_flags: dict[str, bool]
    source_cutoffs: dict[str, datetime]
    observations: tuple[dict[str, Any], ...]


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _decimal(value: object, name: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def _float(value: Decimal | float | int, name: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _state_number(state: StateObservation, key: str) -> Decimal | None:
    value = state.state.get(key)
    if value in (None, ""):
        return None
    return _decimal(value, key)


def _price_descriptor(observation: PriceObservation) -> dict[str, Any]:
    return {
        "kind": "polymarket_price",
        "row_id": observation.row_id,
        "source": observation.source,
        "condition_id": observation.condition_id,
        "asset_id": observation.asset_id,
        "outcome": observation.outcome,
        "observed_at": observation.observed_at,
        "price": observation.price,
        "fidelity_minutes": observation.fidelity_minutes,
    }


def _state_descriptor(observation: StateObservation) -> dict[str, Any]:
    return {
        "kind": "compact_state",
        "row_id": observation.row_id,
        "state_key": observation.state_key,
        "bucket_at": observation.bucket_at,
        "last_event_at": observation.last_event_at,
        "source": observation.source,
        "stream": observation.stream,
        "instrument": observation.instrument,
        "asset_id": observation.asset_id,
        "fresh": observation.fresh,
        "state": observation.state,
    }


def _candle_descriptor(observation: CandleObservation) -> dict[str, Any]:
    return {
        "kind": "btc_candle",
        "row_id": observation.row_id,
        "source": observation.source,
        "market_type": observation.market_type,
        "symbol": observation.symbol,
        "interval_seconds": observation.interval_seconds,
        "bucket_at": observation.bucket_at,
        "open": observation.open,
        "high": observation.high,
        "low": observation.low,
        "close": observation.close,
        "volume": observation.volume,
        "turnover": observation.turnover,
    }


def time_geometry(target: FeatureTarget, feature_at: datetime) -> FeatureGroup:
    start = _utc(target.market_start_at, "market_start_at")
    end = _utc(target.market_end_at, "market_end_at")
    at = _utc(feature_at, "feature_at")
    if target.horizon_seconds <= 0:
        raise ValueError("horizon_seconds must be positive")
    if end <= start:
        raise ValueError("market_end_at must be after market_start_at")
    if not start < at < end:
        raise ValueError("feature_at must be strictly inside market window")
    elapsed = (at - start).total_seconds()
    remaining = (end - at).total_seconds()
    return FeatureGroup(
        values={
            "seconds_elapsed": int(elapsed),
            "seconds_remaining": int(remaining),
            "fraction_elapsed": _float(elapsed / target.horizon_seconds, "fraction_elapsed"),
            "horizon_seconds": target.horizon_seconds,
        },
        missing_flags={},
        source_cutoffs={},
        observations=(),
    )


def polymarket_prices(
    up: PriceObservation | None,
    down: PriceObservation | None,
    feature_at: datetime,
) -> FeatureGroup:
    at = _utc(feature_at, "feature_at")
    values: dict[str, Any] = {
        "pm_up_price": None,
        "pm_down_price": None,
        "pm_price_sum": None,
        "pm_up_minus_down": None,
        "pm_up_price_staleness_s": None,
        "pm_down_price_staleness_s": None,
    }
    missing = {
        "pm_up_price_missing": up is None,
        "pm_down_price_missing": down is None,
    }
    cutoffs: dict[str, datetime] = {}
    observations: list[dict[str, Any]] = []

    if up is not None:
        if up.effective_at > at:
            raise ValueError("Up price observation exceeds feature_at")
        values["pm_up_price"] = _float(up.price, "pm_up_price")
        values["pm_up_price_staleness_s"] = _float(
            (at - up.effective_at).total_seconds(), "pm_up_price_staleness_s"
        )
        cutoffs["polymarket_up_price"] = up.effective_at
        observations.append(_price_descriptor(up))
    if down is not None:
        if down.effective_at > at:
            raise ValueError("Down price observation exceeds feature_at")
        values["pm_down_price"] = _float(down.price, "pm_down_price")
        values["pm_down_price_staleness_s"] = _float(
            (at - down.effective_at).total_seconds(), "pm_down_price_staleness_s"
        )
        cutoffs["polymarket_down_price"] = down.effective_at
        observations.append(_price_descriptor(down))
    if up is not None and down is not None:
        values["pm_price_sum"] = _float(up.price + down.price, "pm_price_sum")
        values["pm_up_minus_down"] = _float(up.price - down.price, "pm_up_minus_down")

    return FeatureGroup(values, missing, cutoffs, tuple(observations))


def _book_keys(prefix: str) -> tuple[str, ...]:
    return (
        f"{prefix}_best_bid",
        f"{prefix}_best_ask",
        f"{prefix}_mid",
        f"{prefix}_spread",
        f"{prefix}_bid_depth",
        f"{prefix}_ask_depth",
        f"{prefix}_book_imbalance",
    )


def book_state(prefix: str, state: StateObservation | None) -> FeatureGroup:
    if not prefix:
        raise ValueError("prefix must not be empty")
    values = dict.fromkeys(_book_keys(prefix))
    missing = {
        f"{prefix}_book_missing": state is None or not state.fresh,
        f"{prefix}_book_stale": state is not None and not state.fresh,
    }
    if state is None:
        return FeatureGroup(values, missing, {}, ())

    cutoffs = {f"{prefix}_book_state": state.effective_at}
    observations = (_state_descriptor(state),)
    if not state.fresh:
        return FeatureGroup(values, missing, cutoffs, observations)

    bid = _state_number(state, "best_bid")
    ask = _state_number(state, "best_ask")
    bid_depth = _state_number(state, "bid_depth")
    ask_depth = _state_number(state, "ask_depth")
    if bid is not None:
        values[f"{prefix}_best_bid"] = _float(bid, f"{prefix}_best_bid")
    if ask is not None:
        values[f"{prefix}_best_ask"] = _float(ask, f"{prefix}_best_ask")
    if bid is not None and ask is not None:
        values[f"{prefix}_mid"] = _float((bid + ask) / Decimal(2), f"{prefix}_mid")
        values[f"{prefix}_spread"] = _float(ask - bid, f"{prefix}_spread")
    if bid_depth is not None:
        values[f"{prefix}_bid_depth"] = _float(bid_depth, f"{prefix}_bid_depth")
    if ask_depth is not None:
        values[f"{prefix}_ask_depth"] = _float(ask_depth, f"{prefix}_ask_depth")
    if bid_depth is not None and ask_depth is not None:
        denominator = bid_depth + ask_depth
        if denominator > 0:
            imbalance = (bid_depth - ask_depth) / denominator
            values[f"{prefix}_book_imbalance"] = _float(
                imbalance, f"{prefix}_book_imbalance"
            )

    missing[f"{prefix}_book_missing"] = bid is None or ask is None
    return FeatureGroup(values, missing, cutoffs, observations)


def _simple_return(latest: Decimal, earlier: Decimal, name: str) -> float:
    if earlier <= 0 or latest <= 0:
        raise ValueError(f"{name} requires positive prices")
    return _float(latest / earlier - Decimal(1), name)


def _realized_volatility(candles: list[CandleObservation], periods: int) -> float | None:
    if len(candles) < periods + 1:
        return None
    closes = [candle.close for candle in candles[-(periods + 1) :]]
    if any(close <= 0 or not close.is_finite() for close in closes):
        raise ValueError("volatility requires positive finite closes")
    returns = [
        math.log(float(current / previous))
        for previous, current in zip(closes, closes[1:], strict=False)
    ]
    value = pstdev(returns)
    return _float(value, f"realized_volatility_{periods}")


def coinbase_candles(
    candles: Iterable[CandleObservation],
    market_start_at: datetime,
) -> FeatureGroup:
    start = _utc(market_start_at, "market_start_at")
    ordered = sorted(candles, key=lambda item: (item.effective_at, item.row_id))
    values: dict[str, Any] = {
        "coinbase_latest_close": None,
        "coinbase_return_1m": None,
        "coinbase_return_5m": None,
        "coinbase_return_15m": None,
        "coinbase_realized_vol_5m": None,
        "coinbase_realized_vol_15m": None,
        "coinbase_return_from_prestart_close": None,
    }
    missing = {"coinbase_candles_missing": True}
    if not ordered:
        return FeatureGroup(values, missing, {}, ())

    latest = ordered[-1]
    if latest.close <= 0 or not latest.close.is_finite():
        raise ValueError("coinbase latest close must be positive and finite")
    values["coinbase_latest_close"] = _float(latest.close, "coinbase_latest_close")
    for periods in (1, 5, 15):
        if len(ordered) >= periods + 1:
            values[f"coinbase_return_{periods}m"] = _simple_return(
                latest.close,
                ordered[-(periods + 1)].close,
                f"coinbase_return_{periods}m",
            )
    values["coinbase_realized_vol_5m"] = _realized_volatility(ordered, 5)
    values["coinbase_realized_vol_15m"] = _realized_volatility(ordered, 15)

    prestart = [candle for candle in ordered if candle.effective_at <= start]
    if prestart:
        values["coinbase_return_from_prestart_close"] = _simple_return(
            latest.close,
            prestart[-1].close,
            "coinbase_return_from_prestart_close",
        )

    missing["coinbase_candles_missing"] = any(
        values[key] is None
        for key in (
            "coinbase_return_1m",
            "coinbase_return_5m",
            "coinbase_return_15m",
            "coinbase_realized_vol_5m",
            "coinbase_realized_vol_15m",
        )
    )
    return FeatureGroup(
        values,
        missing,
        {"coinbase_candles": latest.effective_at},
        tuple(_candle_descriptor(candle) for candle in ordered),
    )


def _fresh_state_values(state: StateObservation | None) -> dict[str, Decimal | None] | None:
    if state is None or not state.fresh:
        return None
    return {
        "last": _state_number(state, "last_price"),
        "best_bid": _state_number(state, "best_bid"),
        "best_ask": _state_number(state, "best_ask"),
        "mark": _state_number(state, "mark_price"),
        "index": _state_number(state, "index_price"),
        "funding_rate": _state_number(state, "funding_rate"),
        "open_interest": _state_number(state, "open_interest"),
    }


def bybit_state(
    spot: StateObservation | None,
    linear: StateObservation | None,
) -> FeatureGroup:
    values: dict[str, Any] = {
        "bybit_spot_last": None,
        "bybit_spot_mid": None,
        "bybit_linear_last": None,
        "bybit_linear_mark": None,
        "bybit_linear_index": None,
        "bybit_linear_funding_rate": None,
        "bybit_linear_open_interest": None,
        "bybit_linear_vs_spot_basis": None,
    }
    missing = {
        "bybit_spot_state_missing": spot is None or not spot.fresh,
        "bybit_linear_state_missing": linear is None or not linear.fresh,
    }
    cutoffs: dict[str, datetime] = {}
    observations: list[dict[str, Any]] = []
    spot_values = _fresh_state_values(spot)
    linear_values = _fresh_state_values(linear)

    if spot is not None:
        cutoffs["bybit_spot_state"] = spot.effective_at
        observations.append(_state_descriptor(spot))
    if linear is not None:
        cutoffs["bybit_linear_state"] = linear.effective_at
        observations.append(_state_descriptor(linear))

    if spot_values is not None:
        if spot_values["last"] is not None:
            values["bybit_spot_last"] = _float(spot_values["last"], "bybit_spot_last")
        bid = spot_values["best_bid"]
        ask = spot_values["best_ask"]
        if bid is not None and ask is not None:
            values["bybit_spot_mid"] = _float((bid + ask) / Decimal(2), "bybit_spot_mid")
    if linear_values is not None:
        mapping = {
            "last": "bybit_linear_last",
            "mark": "bybit_linear_mark",
            "index": "bybit_linear_index",
            "funding_rate": "bybit_linear_funding_rate",
            "open_interest": "bybit_linear_open_interest",
        }
        for source_key, target_key in mapping.items():
            value = linear_values[source_key]
            if value is not None:
                values[target_key] = _float(value, target_key)
    if spot_values is not None and linear_values is not None:
        spot_last = spot_values["last"]
        linear_last = linear_values["last"]
        if spot_last is not None and linear_last is not None:
            values["bybit_linear_vs_spot_basis"] = _simple_return(
                linear_last, spot_last, "bybit_linear_vs_spot_basis"
            )

    return FeatureGroup(values, missing, cutoffs, tuple(observations))


def official_reference() -> FeatureGroup:
    return FeatureGroup(
        values={"official_reference_distance": None},
        missing_flags={"official_reference_missing": True},
        source_cutoffs={},
        observations=(),
    )
