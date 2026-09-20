from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from bp_engine.features.calculators import FeatureGroup
from bp_engine.features.v4_models import BTCStateObservation


@dataclass(frozen=True)
class BTCShortAnchorSet:
    market_start: BTCStateObservation | None
    trailing_120s: BTCStateObservation | None
    trailing_60s: BTCStateObservation | None
    trailing_30s: BTCStateObservation | None
    current: BTCStateObservation | None


@dataclass(frozen=True)
class BTCRegimeAnchorSet:
    trailing_60m: BTCStateObservation | None
    trailing_15m: BTCStateObservation | None
    trailing_5m: BTCStateObservation | None
    current: BTCStateObservation | None


def _effective_at(observation: BTCStateObservation):
    return max(observation.bucket_at, observation.last_event_at)


def _descriptor(observation: BTCStateObservation) -> dict[str, object]:
    return {
        "kind": "btc_compact_state",
        "row_id": observation.row_id,
        "bucket_at": observation.bucket_at,
        "last_event_at": observation.last_event_at,
        "source": observation.source,
        "stream": observation.stream,
        "instrument": observation.instrument,
        "price": observation.price,
        "fresh": observation.fresh,
        "age_seconds": observation.age_seconds,
    }


def _usable(observation: BTCStateObservation | None) -> bool:
    return observation is not None and observation.fresh


def _simple_return(
    current: BTCStateObservation | None,
    anchor: BTCStateObservation | None,
) -> float | None:
    if not _usable(current) or not _usable(anchor):
        return None
    assert current is not None and anchor is not None
    if current.price <= 0 or anchor.price <= 0:
        return None
    value = float(current.price / anchor.price - Decimal(1))
    return value if math.isfinite(value) else None


def short_return_group(prefix: str, anchors: BTCShortAnchorSet) -> FeatureGroup:
    if not prefix:
        raise ValueError("prefix must not be empty")

    values = {
        f"{prefix}_return_from_market_start": _simple_return(
            anchors.current, anchors.market_start
        ),
        f"{prefix}_return_30s": _simple_return(
            anchors.current, anchors.trailing_30s
        ),
        f"{prefix}_return_60s": _simple_return(
            anchors.current, anchors.trailing_60s
        ),
        f"{prefix}_return_120s": _simple_return(
            anchors.current, anchors.trailing_120s
        ),
    }
    items = (
        ("market_start", anchors.market_start),
        ("trailing_120s", anchors.trailing_120s),
        ("trailing_60s", anchors.trailing_60s),
        ("trailing_30s", anchors.trailing_30s),
        ("current", anchors.current),
    )
    missing: dict[str, bool] = {}
    cutoffs = {}
    observations: list[dict[str, object]] = []
    for name, observation in items:
        missing[f"{prefix}_{name}_missing"] = observation is None
        missing[f"{prefix}_{name}_stale"] = (
            observation is not None and not observation.fresh
        )
        if observation is None:
            continue
        cutoffs[f"{prefix}_{name}_state"] = _effective_at(observation)
        observations.append(_descriptor(observation))
    return FeatureGroup(values, missing, cutoffs, tuple(observations))


def regime_return_group(prefix: str, anchors: BTCRegimeAnchorSet) -> FeatureGroup:
    if not prefix:
        raise ValueError("prefix must not be empty")
    values = {
        f"{prefix}_return_5m": _simple_return(
            anchors.current, anchors.trailing_5m
        ),
        f"{prefix}_return_15m": _simple_return(
            anchors.current, anchors.trailing_15m
        ),
        f"{prefix}_return_60m": _simple_return(
            anchors.current, anchors.trailing_60m
        ),
    }
    items = (
        ("regime_trailing_60m", anchors.trailing_60m),
        ("regime_trailing_15m", anchors.trailing_15m),
        ("regime_trailing_5m", anchors.trailing_5m),
    )
    missing: dict[str, bool] = {}
    cutoffs = {}
    observations: list[dict[str, object]] = []
    for name, observation in items:
        missing[f"{prefix}_{name}_missing"] = observation is None
        missing[f"{prefix}_{name}_stale"] = (
            observation is not None and not observation.fresh
        )
        if observation is None:
            continue
        cutoffs[f"{prefix}_{name}_state"] = _effective_at(observation)
        observations.append(_descriptor(observation))
    return FeatureGroup(values, missing, cutoffs, tuple(observations))


def _direction(value: float | None) -> int | None:
    if value is None or not math.isfinite(value):
        return None
    if value > 0.0:
        return 1
    if value < 0.0:
        return -1
    return 0


def _majority_direction(values: tuple[float | None, ...]) -> float | None:
    directions = tuple(_direction(value) for value in values)
    usable = tuple(value for value in directions if value not in (None, 0))
    if len(usable) < 2:
        return None
    positives = sum(value == 1 for value in usable)
    negatives = sum(value == -1 for value in usable)
    if positives >= 2:
        return 1.0
    if negatives >= 2:
        return -1.0
    return 0.0


def _all_venue_agreement(values: tuple[float | None, ...]) -> float | None:
    directions = tuple(_direction(value) for value in values)
    if any(value is None for value in directions):
        return None
    nonzero = tuple(value for value in directions if value != 0)
    if not nonzero:
        return 0.0
    return 1.0 if len(set(nonzero)) == 1 and len(nonzero) == 3 else 0.0


def regime_consensus_group(
    *,
    coinbase: dict[str, float | None],
    bybit_spot: dict[str, float | None],
    bybit_linear: dict[str, float | None],
) -> FeatureGroup:
    horizon_values: dict[str, tuple[float | None, ...]] = {}
    for horizon in ("5m", "15m", "60m"):
        key = f"return_{horizon}"
        horizon_values[horizon] = (
            coinbase.get(key),
            bybit_spot.get(key),
            bybit_linear.get(key),
        )

    directions = {
        horizon: _majority_direction(values)
        for horizon, values in horizon_values.items()
    }
    complete = all(value is not None for value in directions.values())
    bull = complete and all(value == 1.0 for value in directions.values())
    bear = complete and all(value == -1.0 for value in directions.values())
    sideways = complete and not bull and not bear

    values: dict[str, float | None] = {
        "regime_5m_direction": directions["5m"],
        "regime_15m_direction": directions["15m"],
        "regime_60m_direction": directions["60m"],
        "regime_5m_venue_agreement": _all_venue_agreement(horizon_values["5m"]),
        "regime_15m_venue_agreement": _all_venue_agreement(horizon_values["15m"]),
        "regime_60m_venue_agreement": _all_venue_agreement(horizon_values["60m"]),
        "regime_trend_score": (
            sum(float(value) for value in directions.values()) / 3.0
            if complete
            else None
        ),
        "regime_bull": 1.0 if bull else (0.0 if complete else None),
        "regime_bear": 1.0 if bear else (0.0 if complete else None),
        "regime_sideways_mixed": 1.0 if sideways else (0.0 if complete else None),
    }
    missing = {f"{key}_missing": value is None for key, value in values.items()}
    return FeatureGroup(values, missing, {}, ())


def _market_start_return(anchors: BTCShortAnchorSet) -> float | None:
    return _simple_return(anchors.current, anchors.market_start)


def _direction_agreement(left: float | None, right: float | None) -> float | None:
    if left is None or right is None or left == 0.0 or right == 0.0:
        return None
    return 1.0 if (left > 0.0) == (right > 0.0) else 0.0


def _state_float(
    observation: BTCStateObservation | None,
    key: str,
) -> float | None:
    if not _usable(observation):
        return None
    assert observation is not None
    raw = observation.state.get(key)
    if raw in (None, ""):
        return None
    try:
        value = float(Decimal(str(raw)))
    except (InvalidOperation, ValueError):
        return None
    return value if math.isfinite(value) else None


def _basis(
    spot: BTCStateObservation | None,
    linear: BTCStateObservation | None,
) -> float | None:
    if not _usable(spot) or not _usable(linear):
        return None
    assert spot is not None and linear is not None
    if spot.price <= 0 or linear.price <= 0:
        return None
    value = float(linear.price / spot.price - Decimal(1))
    return value if math.isfinite(value) else None


def cross_venue_group(
    coinbase: BTCShortAnchorSet,
    bybit_spot: BTCShortAnchorSet,
    bybit_linear: BTCShortAnchorSet,
) -> FeatureGroup:
    coinbase_return = _market_start_return(coinbase)
    bybit_spot_return = _market_start_return(bybit_spot)
    bybit_linear_return = _market_start_return(bybit_linear)
    spread = (
        coinbase_return - bybit_spot_return
        if coinbase_return is not None and bybit_spot_return is not None
        else None
    )
    values = {
        "coinbase_bybit_spot_return_spread": spread,
        "coinbase_bybit_spot_direction_agree": _direction_agreement(
            coinbase_return, bybit_spot_return
        ),
        "spot_linear_direction_agree": _direction_agreement(
            bybit_spot_return, bybit_linear_return
        ),
        "bybit_linear_vs_spot_basis": _basis(
            bybit_spot.current, bybit_linear.current
        ),
        "bybit_linear_funding_rate": _state_float(
            bybit_linear.current, "funding_rate"
        ),
        "bybit_linear_open_interest": _state_float(
            bybit_linear.current, "open_interest"
        ),
    }
    missing = {f"{key}_missing": value is None for key, value in values.items()}
    return FeatureGroup(values, missing, {}, ())
