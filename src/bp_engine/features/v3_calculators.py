from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from bp_engine.features.calculators import FeatureGroup
from bp_engine.features.v3_models import BTCStateObservation


@dataclass(frozen=True)
class BTCAnchorSet:
    market_start: BTCStateObservation | None
    trailing_120s: BTCStateObservation | None
    trailing_60s: BTCStateObservation | None
    trailing_30s: BTCStateObservation | None
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


def _anchor_items(anchors: BTCAnchorSet):
    return (
        ("market_start", anchors.market_start),
        ("trailing_120s", anchors.trailing_120s),
        ("trailing_60s", anchors.trailing_60s),
        ("trailing_30s", anchors.trailing_30s),
        ("current", anchors.current),
    )


def btc_return_group(prefix: str, anchors: BTCAnchorSet) -> FeatureGroup:
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
    missing: dict[str, bool] = {}
    cutoffs = {}
    observations: list[dict[str, object]] = []
    for name, observation in _anchor_items(anchors):
        missing[f"{prefix}_{name}_missing"] = observation is None
        missing[f"{prefix}_{name}_stale"] = (
            observation is not None and not observation.fresh
        )
        if observation is None:
            continue
        cutoffs[f"{prefix}_{name}_state"] = _effective_at(observation)
        observations.append(_descriptor(observation))

    return FeatureGroup(values, missing, cutoffs, tuple(observations))


def _market_start_return(anchors: BTCAnchorSet) -> float | None:
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


def btc_cross_venue_group(
    coinbase: BTCAnchorSet,
    bybit_spot: BTCAnchorSet,
    bybit_linear: BTCAnchorSet,
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
