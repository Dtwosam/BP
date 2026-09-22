from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, select

from bp_engine.features.hashing import canonical_hash
from bp_engine.features.v4_coverage import (
    REGIME_RETURN_FIELDS,
    SHORT_RETURN_FIELDS,
    SOURCE_PREFIXES,
    build_v4_coverage_report,
)
from bp_engine.storage.schema import market_features
from bp_engine.v4_research.config import (
    FROZEN_V4_GATE_B_CONFIG,
    V4GateBConfig,
    v4_gate_b_config_payload,
)

CURRENT_STATE_AVAILABILITY_THRESHOLD = 0.90
SHORT_RETURN_AVAILABILITY_THRESHOLD = 0.90
REGIME_RETURN_AVAILABILITY_THRESHOLD = 0.75


def _aware_utc(name: str, value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _finite(value: object) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(parsed)


def _eligible_rows(
    connection: Connection,
    *,
    config: V4GateBConfig,
) -> list[Mapping[str, Any]]:
    query = (
        select(
            market_features.c.condition_id,
            market_features.c.feature_offset_seconds,
            market_features.c.features,
        )
        .where(market_features.c.feature_version == config.feature_version)
        .where(market_features.c.market_start_at >= config.epoch_start)
        .where(market_features.c.market_start_at < config.epoch_end)
        .order_by(
            market_features.c.condition_id,
            market_features.c.feature_offset_seconds,
            market_features.c.id,
        )
    )
    return list(connection.execute(query).mappings())


def _source_availability(coverage: Mapping[str, Any]) -> dict[str, float]:
    row_count = int(coverage["row_count"])
    values: dict[str, float] = {}
    for prefix in SOURCE_PREFIXES:
        available = int(
            coverage["sources"][prefix]["current_state"]["available_count"]
        )
        values[prefix] = available / row_count if row_count else 0.0
    return values


def _return_availability(
    rows: list[Mapping[str, Any]],
    fields: tuple[str, ...],
    *,
    ignore_structural_120s_at_60: bool,
) -> dict[str, float]:
    values: dict[str, float] = {}
    for field in fields:
        eligible = [
            row
            for row in rows
            if not (
                ignore_structural_120s_at_60
                and field.endswith("_return_120s")
                and int(row["feature_offset_seconds"]) == 60
            )
        ]
        available = sum(
            1
            for row in eligible
            if _finite(dict(row["features"] or {}).get(field))
        )
        values[field] = available / len(eligible) if eligible else 0.0
    return values


def _has_complete_offsets(
    rows: list[Mapping[str, Any]],
    *,
    expected_offsets: tuple[int, ...],
) -> bool:
    observed: dict[str, set[int]] = {}
    for row in rows:
        condition_id = str(row["condition_id"])
        observed.setdefault(condition_id, set()).add(
            int(row["feature_offset_seconds"])
        )
    expected = set(expected_offsets)
    return bool(observed) and all(offsets == expected for offsets in observed.values())


def assess_v4_gate_b_readiness(
    connection: Connection,
    *,
    as_of: datetime,
    config: V4GateBConfig = FROZEN_V4_GATE_B_CONFIG,
) -> dict[str, Any]:
    checked_at = _aware_utc("as_of", as_of)
    coverage = build_v4_coverage_report(
        connection,
        epoch_start=config.epoch_start,
        epoch_end=config.epoch_end,
    )
    rows = _eligible_rows(connection, config=config)
    source_availability = _source_availability(coverage)
    short_return_availability = _return_availability(
        rows,
        SHORT_RETURN_FIELDS,
        ignore_structural_120s_at_60=True,
    )
    regime_return_availability = _return_availability(
        rows,
        REGIME_RETURN_FIELDS,
        ignore_structural_120s_at_60=False,
    )
    regime_market_counts = {
        regime: int(coverage["regime"][regime]["market_count"])
        for regime in config.known_regimes
    }

    blockers: set[str] = set()
    if checked_at < config.epoch_end:
        blockers.add("epoch_incomplete")
    if int(coverage["future_cutoff_violation_count"]) > 0:
        blockers.add("future_cutoff_violations")
    if int(coverage["polymarket_predictor_key_count"]) > 0:
        blockers.add("polymarket_predictor_keys_present")
    if int(coverage["regime_invariant_violation_count"]) > 0:
        blockers.add("regime_invariant_violations")
    if not _has_complete_offsets(
        rows,
        expected_offsets=config.feature_offsets_seconds,
    ):
        blockers.add("incomplete_market_offsets")
    if not rows:
        blockers.add("no_eligible_markets")

    for prefix, availability in source_availability.items():
        if availability < CURRENT_STATE_AVAILABILITY_THRESHOLD:
            blockers.add(f"{prefix}_current_state_availability_below_0.90")
    for field, availability in short_return_availability.items():
        if availability < SHORT_RETURN_AVAILABILITY_THRESHOLD:
            blockers.add(f"{field}_availability_below_0.90")
    for field, availability in regime_return_availability.items():
        if availability < REGIME_RETURN_AVAILABILITY_THRESHOLD:
            blockers.add(f"{field}_availability_below_0.75")
    for regime, count in regime_market_counts.items():
        if count < config.min_known_regime_markets:
            blockers.add(f"{regime}_market_count_below_minimum")

    blocking_reasons = tuple(sorted(blockers))
    readiness_input_sha256 = canonical_hash(
        {
            "config": v4_gate_b_config_payload(config),
            "as_of": checked_at,
            "coverage_input_sha256": coverage["coverage_input_sha256"],
            "current_state_availability_threshold": (
                CURRENT_STATE_AVAILABILITY_THRESHOLD
            ),
            "short_return_availability_threshold": (
                SHORT_RETURN_AVAILABILITY_THRESHOLD
            ),
            "regime_return_availability_threshold": (
                REGIME_RETURN_AVAILABILITY_THRESHOLD
            ),
            "source_availability": source_availability,
            "short_return_availability": short_return_availability,
            "regime_return_availability": regime_return_availability,
            "regime_market_counts": regime_market_counts,
            "blocking_reasons": blocking_reasons,
        }
    )
    return {
        "ready": not blocking_reasons,
        "blocking_reasons": blocking_reasons,
        "epoch_complete": checked_at >= config.epoch_end,
        "coverage": coverage,
        "source_availability": source_availability,
        "short_return_availability": short_return_availability,
        "regime_return_availability": regime_return_availability,
        "regime_market_counts": regime_market_counts,
        "readiness_input_sha256": readiness_input_sha256,
        "labels_read": False,
        "training_performed": False,
        "policy_selected": False,
    }
