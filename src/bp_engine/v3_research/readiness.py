from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, select

from bp_engine.features.hashing import canonical_hash
from bp_engine.features.v3_coverage import (
    RETURN_FIELDS,
    SOURCE_PREFIXES,
    build_v3_coverage_report,
)
from bp_engine.storage.schema import market_features
from bp_engine.v3_research.config import FROZEN_V3_GATE_B_CONFIG, V3GateBConfig
from bp_engine.v3_research.exclusions import (
    ExclusionManifest,
    ExclusionManifestError,
    build_exclusion_manifest,
)

READINESS_AVAILABILITY_THRESHOLD = 0.90


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


def _validated_manifest(
    manifest: ExclusionManifest,
    *,
    expected_kind: str,
) -> ExclusionManifest:
    rebuilt = build_exclusion_manifest(
        kind=expected_kind,
        condition_ids=manifest.condition_ids,
    )
    if manifest != rebuilt:
        raise ExclusionManifestError(
            f"{expected_kind} exclusion manifest does not match its hash-bound payload"
        )
    return manifest


def _eligible_rows(
    connection: Connection,
    *,
    config: V3GateBConfig,
    excluded_condition_ids: tuple[str, ...],
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
    )
    if excluded_condition_ids:
        query = query.where(
            market_features.c.condition_id.not_in(excluded_condition_ids)
        )
    return list(
        connection.execute(
            query.order_by(
                market_features.c.condition_id,
                market_features.c.feature_offset_seconds,
                market_features.c.id,
            )
        ).mappings()
    )


def _source_availability(coverage: Mapping[str, Any]) -> dict[str, float]:
    row_count = int(coverage["row_count"])
    values: dict[str, float] = {}
    for prefix in SOURCE_PREFIXES:
        available = int(coverage["sources"][prefix]["current_state"]["available_count"])
        values[prefix] = available / row_count if row_count else 0.0
    return values


def _non_structural_return_availability(
    rows: list[Mapping[str, Any]],
) -> dict[str, float]:
    values: dict[str, float] = {}
    for field in RETURN_FIELDS:
        eligible = [
            row
            for row in rows
            if not (
                field.endswith("_return_120s")
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


def _config_hash_payload(config: V3GateBConfig) -> dict[str, object]:
    return {
        "research_plan_version": config.research_plan_version,
        "dataset_version": config.dataset_version,
        "feature_version": config.feature_version,
        "label_version": config.label_version,
        "horizon_seconds": config.horizon_seconds,
        "feature_offsets_seconds": config.feature_offsets_seconds,
        "epoch_start": config.epoch_start,
        "epoch_end": config.epoch_end,
        "train_duration_seconds": config.train_duration.total_seconds(),
        "validation_duration_seconds": config.validation_duration.total_seconds(),
        "test_duration_seconds": config.test_duration.total_seconds(),
        "step_duration_seconds": config.step_duration.total_seconds(),
        "final_holdout_duration_seconds": config.final_holdout_duration.total_seconds(),
        "embargo_markets": config.embargo_markets,
        "min_train_markets": config.min_train_markets,
        "min_validation_markets": config.min_validation_markets,
        "min_test_markets": config.min_test_markets,
        "min_final_holdout_markets": config.min_final_holdout_markets,
        "ordinary_fold_count": config.ordinary_fold_count,
    }


def assess_v3_gate_b_readiness(
    connection: Connection,
    *,
    as_of: datetime,
    diagnosis_exclusions: ExclusionManifest,
    consumed_v2_final_holdout_exclusions: ExclusionManifest,
    config: V3GateBConfig = FROZEN_V3_GATE_B_CONFIG,
) -> dict[str, Any]:
    checked_at = _aware_utc("as_of", as_of)
    diagnosis = _validated_manifest(
        diagnosis_exclusions,
        expected_kind="diagnosis",
    )
    consumed = _validated_manifest(
        consumed_v2_final_holdout_exclusions,
        expected_kind="consumed_v2_final_holdout",
    )
    excluded_condition_ids = tuple(
        sorted(set(diagnosis.condition_ids) | set(consumed.condition_ids))
    )

    coverage = build_v3_coverage_report(
        connection,
        epoch_start=config.epoch_start,
        epoch_end=config.epoch_end,
        excluded_condition_ids=excluded_condition_ids,
    )
    rows = _eligible_rows(
        connection,
        config=config,
        excluded_condition_ids=excluded_condition_ids,
    )
    source_availability = _source_availability(coverage)
    return_availability = _non_structural_return_availability(rows)

    blockers: set[str] = set()
    if checked_at < config.epoch_end:
        blockers.add("epoch_incomplete")
    if int(coverage["future_cutoff_violation_count"]) > 0:
        blockers.add("future_cutoff_violations")
    if int(coverage["polymarket_predictor_key_count"]) > 0:
        blockers.add("polymarket_predictor_keys_present")
    if not _has_complete_offsets(
        rows,
        expected_offsets=config.feature_offsets_seconds,
    ):
        blockers.add("incomplete_market_offsets")
    if not rows:
        blockers.add("no_eligible_markets")

    for prefix, availability in source_availability.items():
        if availability < READINESS_AVAILABILITY_THRESHOLD:
            blockers.add(f"{prefix}_current_state_availability_below_0.90")
    for field, availability in return_availability.items():
        if availability < READINESS_AVAILABILITY_THRESHOLD:
            blockers.add(f"{field}_availability_below_0.90")

    blocking_reasons = tuple(sorted(blockers))
    readiness_input_sha256 = canonical_hash(
        {
            "config": _config_hash_payload(config),
            "as_of": checked_at,
            "coverage_input_sha256": coverage["coverage_input_sha256"],
            "diagnosis_exclusion_sha256": diagnosis.sha256,
            "consumed_v2_final_holdout_exclusion_sha256": consumed.sha256,
            "excluded_condition_ids": excluded_condition_ids,
            "availability_threshold": READINESS_AVAILABILITY_THRESHOLD,
            "source_availability": source_availability,
            "non_structural_return_availability": return_availability,
            "blocking_reasons": blocking_reasons,
        }
    )
    return {
        "ready": not blocking_reasons,
        "blocking_reasons": blocking_reasons,
        "epoch_complete": checked_at >= config.epoch_end,
        "coverage": coverage,
        "source_availability": source_availability,
        "non_structural_return_availability": return_availability,
        "diagnosis_exclusion_sha256": diagnosis.sha256,
        "consumed_v2_final_holdout_exclusion_sha256": consumed.sha256,
        "readiness_input_sha256": readiness_input_sha256,
    }
