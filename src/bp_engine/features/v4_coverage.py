from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from statistics import median
from typing import Any

from sqlalchemy import Connection, select

from bp_engine.features.hashing import canonical_hash
from bp_engine.features.v4_models import V4_FEATURE_VERSION
from bp_engine.storage.schema import market_features

SOURCE_PREFIXES = ("coinbase", "bybit_spot", "bybit_linear")
SHORT_RETURN_FIELDS = tuple(
    f"{prefix}_{suffix}"
    for prefix in SOURCE_PREFIXES
    for suffix in (
        "return_from_market_start",
        "return_30s",
        "return_60s",
        "return_120s",
    )
)
REGIME_RETURN_FIELDS = tuple(
    f"{prefix}_return_{horizon}"
    for prefix in SOURCE_PREFIXES
    for horizon in ("5m", "15m", "60m")
)
REGIME_NAMES = ("bull", "bear", "sideways_mixed", "unknown")


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
    return result if math.isfinite(result) else None


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
        "feature_version": V4_FEATURE_VERSION,
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


def _availability_report(
    rows: list[Mapping[str, Any]],
    *,
    missing_key: str,
    stale_key: str,
    cutoff_key: str,
) -> dict[str, Any]:
    missing_count = 0
    stale_count = 0
    ages: list[float] = []
    for row in rows:
        flags = dict(row["missing_flags"] or {})
        cutoffs = dict(row["source_cutoffs"] or {})
        if bool(flags.get(missing_key, True)):
            missing_count += 1
        if bool(flags.get(stale_key, False)):
            stale_count += 1
        cutoff = _parse_timestamp(cutoffs.get(cutoff_key))
        if cutoff is not None:
            ages.append((_utc(row["feature_at"]) - cutoff).total_seconds())
    return {
        "available_count": len(rows) - missing_count,
        "missing_count": missing_count,
        "stale_count": stale_count,
        "age_s": summarize_finite(ages),
    }


def _source_report(rows: list[Mapping[str, Any]], prefix: str) -> dict[str, Any]:
    report = {
        "current_state": _availability_report(
            rows,
            missing_key=f"{prefix}_current_missing",
            stale_key=f"{prefix}_current_stale",
            cutoff_key=f"{prefix}_current_state",
        )
    }
    for horizon in ("5m", "15m", "60m"):
        report[f"trailing_{horizon}"] = _availability_report(
            rows,
            missing_key=f"{prefix}_regime_trailing_{horizon}_missing",
            stale_key=f"{prefix}_regime_trailing_{horizon}_stale",
            cutoff_key=f"{prefix}_regime_trailing_{horizon}_state",
        )
    return report


def _return_report(
    rows: list[Mapping[str, Any]], fields: Iterable[str]
) -> dict[str, dict[str, int]]:
    report: dict[str, dict[str, int]] = {}
    for field in fields:
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


def _regime_label(features: Mapping[str, Any]) -> tuple[str, bool]:
    values = {
        "bull": _finite(features.get("regime_bull")),
        "bear": _finite(features.get("regime_bear")),
        "sideways_mixed": _finite(features.get("regime_sideways_mixed")),
    }
    if any(value is None for value in values.values()):
        return "unknown", False
    active = tuple(name for name, value in values.items() if value == 1.0)
    if len(active) == 1:
        return active[0], False
    return "unknown", True


def _regime_report(rows: list[Mapping[str, Any]]) -> tuple[dict[str, Any], int]:
    grouped: dict[str, list[Mapping[str, Any]]] = {name: [] for name in REGIME_NAMES}
    invariant_violations = 0
    for row in rows:
        label, invalid = _regime_label(dict(row["features"] or {}))
        grouped[label].append(row)
        invariant_violations += int(invalid)
    report: dict[str, Any] = {}
    for name, entries in grouped.items():
        report[name] = {
            "row_count": len(entries),
            "market_count": len({str(row["condition_id"]) for row in entries}),
        }
    return report, invariant_violations


def build_v4_coverage_report(
    connection: Connection,
    *,
    epoch_start: datetime | None = None,
    epoch_end: datetime | None = None,
) -> dict[str, Any]:
    if epoch_start is not None and epoch_end is not None:
        if _utc(epoch_end) <= _utc(epoch_start):
            raise ValueError("epoch_end must be after epoch_start")

    query = select(
        market_features.c.condition_id,
        market_features.c.market_start_at,
        market_features.c.feature_at,
        market_features.c.feature_offset_seconds,
        market_features.c.features,
        market_features.c.missing_flags,
        market_features.c.source_cutoffs,
        market_features.c.input_fingerprint,
        market_features.c.feature_hash,
    ).where(market_features.c.feature_version == V4_FEATURE_VERSION)
    if epoch_start is not None:
        query = query.where(market_features.c.market_start_at >= _utc(epoch_start))
    if epoch_end is not None:
        query = query.where(market_features.c.market_start_at < _utc(epoch_end))

    rows = list(
        connection.execute(
            query.order_by(
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
        feature_at = _utc(row["feature_at"])
        for raw in dict(row["source_cutoffs"] or {}).values():
            cutoff = _parse_timestamp(raw)
            if cutoff is not None and cutoff > feature_at:
                future_cutoffs += 1

    regime, regime_invariant_violations = _regime_report(rows)
    predictor_keys = _polymarket_predictor_keys(rows)
    return {
        "feature_version": V4_FEATURE_VERSION,
        "row_count": len(rows),
        "market_count": len({str(row["condition_id"]) for row in rows}),
        "offsets": offsets,
        "by_offset": by_offset,
        "sources": {prefix: _source_report(rows, prefix) for prefix in SOURCE_PREFIXES},
        "short_returns": _return_report(rows, SHORT_RETURN_FIELDS),
        "regime_returns": _return_report(rows, REGIME_RETURN_FIELDS),
        "regime": regime,
        "regime_invariant_violation_count": regime_invariant_violations,
        "future_cutoff_violation_count": future_cutoffs,
        "polymarket_predictor_key_count": len(predictor_keys),
        "coverage_input_sha256": _coverage_hash(rows),
        "policy_selected": False,
        "training_run": False,
        "automatic_promotion": False,
    }
