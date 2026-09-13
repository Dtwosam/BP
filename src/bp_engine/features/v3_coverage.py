from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from statistics import median
from typing import Any

from sqlalchemy import Connection, select

from bp_engine.features.hashing import canonical_hash
from bp_engine.features.v3_models import V3_FEATURE_VERSION
from bp_engine.storage.schema import market_features

SOURCE_PREFIXES = ("coinbase", "bybit_spot", "bybit_linear")
RETURN_FIELDS = tuple(
    f"{prefix}_{suffix}"
    for prefix in SOURCE_PREFIXES
    for suffix in (
        "return_from_market_start",
        "return_30s",
        "return_60s",
        "return_120s",
    )
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return _utc(value)
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = f"{candidate[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _finite(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(result):
        return None
    return result


def summarize_finite(values: Iterable[object]) -> dict[str, float | int | None]:
    ordered = sorted(value for raw in values if (value := _finite(raw)) is not None)
    if not ordered:
        return {"count": 0, "min": None, "median": None, "p90": None, "max": None}
    rank = max(0, math.ceil(0.90 * len(ordered)) - 1)
    return {
        "count": len(ordered),
        "min": float(ordered[0]),
        "median": float(median(ordered)),
        "p90": float(ordered[rank]),
        "max": float(ordered[-1]),
    }


def _coverage_descriptor(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "condition_id": str(row["condition_id"]),
        "feature_at": _utc(row["feature_at"]),
        "feature_offset_seconds": int(row["feature_offset_seconds"]),
        "feature_version": V3_FEATURE_VERSION,
        "missing_flags": dict(row["missing_flags"] or {}),
        "source_cutoffs": dict(row["source_cutoffs"] or {}),
        "input_fingerprint": str(row["input_fingerprint"]),
        "feature_hash": str(row["feature_hash"]),
    }


def _coverage_hash(rows: Iterable[Mapping[str, Any]]) -> str:
    descriptors = [_coverage_descriptor(row) for row in rows]
    descriptors.sort(
        key=lambda value: (
            value["condition_id"],
            value["feature_at"].isoformat(),
            value["feature_offset_seconds"],
            value["input_fingerprint"],
            value["feature_hash"],
        )
    )
    return canonical_hash(descriptors)


def _source_report(rows: list[Mapping[str, Any]], prefix: str) -> dict[str, Any]:
    missing_key = f"{prefix}_current_missing"
    stale_key = f"{prefix}_current_stale"
    cutoff_key = f"{prefix}_current_state"
    missing_count = 0
    stale_count = 0
    ages: list[float] = []

    for row in rows:
        flags = dict(row["missing_flags"] or {})
        cutoffs = dict(row["source_cutoffs"] or {})
        missing = bool(flags.get(missing_key, True))
        if missing:
            missing_count += 1
        if bool(flags.get(stale_key, False)):
            stale_count += 1
        cutoff = _parse_timestamp(cutoffs.get(cutoff_key))
        if cutoff is not None:
            ages.append((_utc(row["feature_at"]) - cutoff).total_seconds())

    return {
        "current_state": {
            "available_count": len(rows) - missing_count,
            "missing_count": missing_count,
            "stale_count": stale_count,
            "age_s": summarize_finite(ages),
        }
    }


def _return_report(rows: list[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    report: dict[str, dict[str, int]] = {}
    for field in RETURN_FIELDS:
        available = sum(
            1
            for row in rows
            if _finite(dict(row["features"] or {}).get(field)) is not None
        )
        report[field] = {
            "available_count": available,
            "missing_count": len(rows) - available,
        }
    return report


def _polymarket_predictor_keys(rows: Iterable[Mapping[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for row in rows:
        for key in dict(row["features"] or {}):
            lowered = str(key).lower()
            if lowered.startswith("pm_") or "polymarket" in lowered:
                keys.add(str(key))
    return keys


def build_v3_coverage_report(connection: Connection) -> dict[str, Any]:
    rows = list(
        connection.execute(
            select(
                market_features.c.condition_id,
                market_features.c.feature_at,
                market_features.c.feature_offset_seconds,
                market_features.c.features,
                market_features.c.missing_flags,
                market_features.c.source_cutoffs,
                market_features.c.input_fingerprint,
                market_features.c.feature_hash,
            )
            .where(market_features.c.feature_version == V3_FEATURE_VERSION)
            .order_by(
                market_features.c.condition_id,
                market_features.c.feature_at,
                market_features.c.id,
            )
        ).mappings()
    )

    offsets = sorted({int(row["feature_offset_seconds"]) for row in rows})
    by_offset: dict[str, dict[str, int]] = {}
    for offset in offsets:
        subset = [row for row in rows if int(row["feature_offset_seconds"]) == offset]
        by_offset[str(offset)] = {
            "row_count": len(subset),
            "market_count": len({str(row["condition_id"]) for row in subset}),
        }

    future_cutoffs = 0
    for row in rows:
        at = _utc(row["feature_at"])
        for raw in dict(row["source_cutoffs"] or {}).values():
            cutoff = _parse_timestamp(raw)
            if cutoff is not None and cutoff > at:
                future_cutoffs += 1

    sources = {prefix: _source_report(rows, prefix) for prefix in SOURCE_PREFIXES}
    predictor_keys = _polymarket_predictor_keys(rows)
    return {
        "feature_version": V3_FEATURE_VERSION,
        "row_count": len(rows),
        "market_count": len({str(row["condition_id"]) for row in rows}),
        "offsets": offsets,
        "by_offset": by_offset,
        "sources": sources,
        "returns": _return_report(rows),
        "future_cutoff_violation_count": future_cutoffs,
        "polymarket_predictor_key_count": len(predictor_keys),
        "coverage_input_sha256": _coverage_hash(rows),
        "policy_selected": False,
        "training_run": False,
        "automatic_promotion": False,
    }
