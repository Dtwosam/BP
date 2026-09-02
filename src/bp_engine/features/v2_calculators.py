from __future__ import annotations

import math
from datetime import UTC, datetime

from bp_engine.features.calculators import FeatureGroup
from bp_engine.features.v2_models import LastTradeObservation


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _finite_float(value: object, name: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def last_trade_features(
    prefix: str,
    observation: LastTradeObservation | None,
    feature_at: datetime,
) -> FeatureGroup:
    if not prefix:
        raise ValueError("prefix must not be empty")
    at = _utc(feature_at, "feature_at")
    values = {
        f"{prefix}_last_trade_price": None,
        f"{prefix}_last_trade_source_age_s": None,
        f"{prefix}_last_trade_availability_age_s": None,
    }
    missing = {f"{prefix}_last_trade_missing": observation is None}
    if observation is None:
        return FeatureGroup(values, missing, {}, ())

    source_at = _utc(observation.source_at, "last_trade_source_at")
    received_at = _utc(observation.received_at, "last_trade_received_at")
    if source_at > at or received_at > at:
        raise ValueError("last-trade evidence exceeds feature_at")

    values[f"{prefix}_last_trade_price"] = _finite_float(
        observation.price, f"{prefix}_last_trade_price"
    )
    values[f"{prefix}_last_trade_source_age_s"] = _finite_float(
        (at - source_at).total_seconds(), f"{prefix}_last_trade_source_age_s"
    )
    values[f"{prefix}_last_trade_availability_age_s"] = _finite_float(
        (at - received_at).total_seconds(), f"{prefix}_last_trade_availability_age_s"
    )
    descriptor = {
        "kind": "polymarket_last_trade",
        "asset_id": observation.asset_id,
        "price": observation.price,
        "size": observation.size,
        "side": observation.side,
        "source_at": source_at,
        "received_at": received_at,
        "event_dedupe_key": observation.event_dedupe_key,
        "compact_state_row_id": observation.compact_state_row_id,
        "compact_state_bucket_at": _utc(
            observation.compact_state_bucket_at, "compact_state_bucket_at"
        ),
        "compact_state_last_event_at": _utc(
            observation.compact_state_last_event_at, "compact_state_last_event_at"
        ),
    }
    return FeatureGroup(
        values=values,
        missing_flags=missing,
        source_cutoffs={
            f"{prefix}_last_trade_source": source_at,
            f"{prefix}_last_trade_received": received_at,
        },
        observations=(descriptor,),
    )
