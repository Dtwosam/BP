from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, and_, select

from bp_engine.features.hashing import canonical_hash
from bp_engine.modeling.models import DATASET_VERSION, DatasetSnapshot, SupervisedRow
from bp_engine.storage.schema import market_features, market_labels


class DatasetIntegrityError(RuntimeError):
    """Raised when frozen feature/label rows cannot form a safe supervised dataset."""


_FORBIDDEN_PREDICTOR_KEYS = {
    "official_outcome",
    "start_reference",
    "end_reference",
    "resolution_source",
    "rules_hash",
    "label_source",
    "label_version",
    "source_snapshot_sha256",
    "source_observed_at",
    "generated_at",
    "condition_id",
    "slug",
    "market_start_at",
    "market_end_at",
    "feature_at",
    "feature_hash",
    "input_fingerprint",
}


def _utc_input(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _utc_db(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _numeric(value: Any, key: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if not isinstance(value, (int, float)):
        raise DatasetIntegrityError(f"predictor {key!r} must be numeric or null")
    result = float(value)
    if not math.isfinite(result):
        raise DatasetIntegrityError(f"predictor {key!r} must be finite")
    return result


def _validate_predictor_key(key: str) -> None:
    if key in _FORBIDDEN_PREDICTOR_KEYS:
        raise DatasetIntegrityError(f"forbidden predictor key: {key}")


def load_dataset(
    connection: Connection,
    *,
    start: datetime,
    end: datetime,
    horizon_seconds: int,
    feature_version: str,
    label_version: str,
) -> DatasetSnapshot:
    start_utc = _utc_input(start, "start")
    end_utc = _utc_input(end, "end")
    if end_utc <= start_utc:
        raise ValueError("start must be before end")
    if horizon_seconds <= 0:
        raise ValueError("horizon_seconds must be positive")

    query = (
        select(
            market_features.c.condition_id,
            market_features.c.slug,
            market_features.c.horizon_seconds.label("feature_horizon_seconds"),
            market_features.c.market_start_at.label("feature_market_start_at"),
            market_features.c.market_end_at.label("feature_market_end_at"),
            market_features.c.feature_at,
            market_features.c.feature_offset_seconds,
            market_features.c.features,
            market_features.c.missing_flags,
            market_features.c.feature_hash,
            market_features.c.input_fingerprint,
            market_labels.c.slug.label("label_slug"),
            market_labels.c.horizon_seconds.label("label_horizon_seconds"),
            market_labels.c.market_start_at.label("label_market_start_at"),
            market_labels.c.market_end_at.label("label_market_end_at"),
            market_labels.c.official_outcome,
            market_labels.c.source_observed_at,
        )
        .select_from(
            market_features.join(
                market_labels,
                and_(
                    market_features.c.condition_id == market_labels.c.condition_id,
                    market_labels.c.label_version == label_version,
                ),
            )
        )
        .where(
            market_features.c.feature_version == feature_version,
            market_features.c.horizon_seconds == horizon_seconds,
            market_features.c.market_start_at >= start_utc,
            market_features.c.market_start_at < end_utc,
        )
        .order_by(
            market_features.c.market_start_at,
            market_features.c.condition_id,
            market_features.c.feature_at,
        )
    )
    records = connection.execute(query).mappings().all()

    rows: list[SupervisedRow] = []
    expected_feature_keys: tuple[str, ...] | None = None
    expected_missing_keys: tuple[str, ...] | None = None

    for record in records:
        feature_start = _utc_db(record["feature_market_start_at"])
        feature_end = _utc_db(record["feature_market_end_at"])
        label_start = _utc_db(record["label_market_start_at"])
        label_end = _utc_db(record["label_market_end_at"])
        if (
            record["slug"] != record["label_slug"]
            or record["feature_horizon_seconds"] != record["label_horizon_seconds"]
            or feature_start != label_start
            or feature_end != label_end
        ):
            raise DatasetIntegrityError(
                f"static market metadata mismatch for {record['condition_id']}"
            )

        source_observed_at = _utc_db(record["source_observed_at"])
        if source_observed_at < label_end:
            raise DatasetIntegrityError(
                f"label source_observed_at precedes market end for {record['condition_id']}"
            )

        outcome = record["official_outcome"]
        if outcome == "Up":
            target = 1
        elif outcome == "Down":
            target = 0
        else:
            raise DatasetIntegrityError(f"unsupported official outcome: {outcome!r}")

        features = dict(record["features"] or {})
        missing_flags = dict(record["missing_flags"] or {})
        feature_keys = tuple(sorted(features))
        missing_keys = tuple(sorted(missing_flags))
        if expected_feature_keys is None:
            expected_feature_keys = feature_keys
            expected_missing_keys = missing_keys
        elif feature_keys != expected_feature_keys or missing_keys != expected_missing_keys:
            raise DatasetIntegrityError("feature or missing-flag key set changed within dataset")

        predictors: dict[str, float | None] = {}
        for key in feature_keys:
            _validate_predictor_key(key)
            predictors[key] = _numeric(features[key], key)
        for key in missing_keys:
            _validate_predictor_key(key)
            value = missing_flags[key]
            if not isinstance(value, bool):
                raise DatasetIntegrityError(f"missing flag {key!r} must be boolean")
            predictors[f"missing__{key}"] = float(value)

        row = SupervisedRow(
            condition_id=record["condition_id"],
            slug=record["slug"],
            horizon_seconds=record["feature_horizon_seconds"],
            market_start_at=feature_start,
            market_end_at=feature_end,
            feature_at=_utc_db(record["feature_at"]),
            feature_offset_seconds=record["feature_offset_seconds"],
            predictors=predictors,
            target=target,
            feature_hash=record["feature_hash"],
            input_fingerprint=record["input_fingerprint"],
        )
        rows.append(row)

    predictor_names = tuple(sorted(rows[0].predictors)) if rows else ()
    descriptors = [
        {
            "condition_id": row.condition_id,
            "feature_at": row.feature_at,
            "feature_offset_seconds": row.feature_offset_seconds,
            "target": row.target,
            "feature_hash": row.feature_hash,
            "input_fingerprint": row.input_fingerprint,
            "predictors": row.predictors,
        }
        for row in rows
    ]
    dataset_sha256 = canonical_hash(
        {
            "dataset_version": DATASET_VERSION,
            "feature_version": feature_version,
            "label_version": label_version,
            "horizon_seconds": horizon_seconds,
            "start": start_utc,
            "end": end_utc,
            "predictor_names": predictor_names,
            "rows": descriptors,
        }
    )
    return DatasetSnapshot(
        dataset_version=DATASET_VERSION,
        feature_version=feature_version,
        label_version=label_version,
        horizon_seconds=horizon_seconds,
        start=start_utc,
        end=end_utc,
        rows=tuple(rows),
        predictor_names=predictor_names,
        dataset_sha256=dataset_sha256,
    )
