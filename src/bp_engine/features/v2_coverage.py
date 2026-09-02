from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from statistics import median
from typing import Any

from sqlalchemy import Connection, select

from bp_engine.features.hashing import canonical_hash
from bp_engine.features.v2_models import V2_FEATURE_VERSION
from bp_engine.storage.schema import market_features


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


def _invalid_numeric_count(features: Mapping[str, Any]) -> int:
    count = 0
    for value in features.values():
        if value is None:
            continue
        if _finite(value) is None:
            count += 1
    return count


def _coverage_descriptor(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "condition_id": str(row["condition_id"]),
        "feature_at": _utc(row["feature_at"]),
        "feature_offset_seconds": int(row["feature_offset_seconds"]),
        "feature_version": V2_FEATURE_VERSION,
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


def _side_report(rows: list[Mapping[str, Any]], side: str) -> dict[str, Any]:
    trade_missing_key = f"pm_{side}_last_trade_missing"
    source_age_key = f"pm_{side}_last_trade_source_age_s"
    availability_age_key = f"pm_{side}_last_trade_availability_age_s"
    book_missing_key = f"pm_{side}_book_missing"
    book_stale_key = f"pm_{side}_book_stale"
    book_cutoff_key = f"pm_{side}_book_state"

    trade_missing = 0
    source_ages: list[object] = []
    availability_ages: list[object] = []
    book_missing = 0
    book_stale = 0
    book_ages: list[float] = []

    for row in rows:
        features = dict(row["features"] or {})
        flags = dict(row["missing_flags"] or {})
        cutoffs = dict(row["source_cutoffs"] or {})
        if bool(flags.get(trade_missing_key, True)):
            trade_missing += 1
        source_ages.append(features.get(source_age_key))
        availability_ages.append(features.get(availability_age_key))

        if bool(flags.get(book_missing_key, True)):
            book_missing += 1
        if bool(flags.get(book_stale_key, False)):
            book_stale += 1
        cutoff = _parse_timestamp(cutoffs.get(book_cutoff_key))
        if cutoff is not None:
            book_ages.append((_utc(row["feature_at"]) - cutoff).total_seconds())

    return {
        "last_trade": {
            "available_count": len(rows) - trade_missing,
            "missing_count": trade_missing,
            "source_age_s": summarize_finite(source_ages),
            "availability_age_s": summarize_finite(availability_ages),
        },
        "book": {
            "available_count": len(rows) - book_missing,
            "missing_count": book_missing,
            "stale_count": book_stale,
            "age_s": summarize_finite(book_ages),
        },
    }


def build_v2_coverage_report(connection: Connection) -> dict[str, Any]:
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
            .where(market_features.c.feature_version == V2_FEATURE_VERSION)
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

    invalid_count = sum(_invalid_numeric_count(dict(row["features"] or {})) for row in rows)
    future_cutoffs = 0
    for row in rows:
        at = _utc(row["feature_at"])
        for raw in dict(row["source_cutoffs"] or {}).values():
            cutoff = _parse_timestamp(raw)
            if cutoff is not None and cutoff > at:
                future_cutoffs += 1

    up = _side_report(rows, "up")
    down = _side_report(rows, "down")
    return {
        "feature_version": V2_FEATURE_VERSION,
        "row_count": len(rows),
        "market_count": len({str(row["condition_id"]) for row in rows}),
        "offsets": offsets,
        "by_offset": by_offset,
        "last_trade": {"up": up["last_trade"], "down": down["last_trade"]},
        "book": {"up": up["book"], "down": down["book"]},
        "invalid_nonfinite_value_count": invalid_count,
        "future_cutoff_violation_count": future_cutoffs,
        "coverage_input_sha256": _coverage_hash(rows),
        "policy_selected": False,
        "automatic_promotion": False,
    }
