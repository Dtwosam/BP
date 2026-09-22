from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Connection, select

from bp_engine.features.hashing import canonical_hash
from bp_engine.storage.schema import market_features
from bp_engine.v4_research.config import (
    FROZEN_V4_GATE_B_CONFIG,
    V4GateBConfig,
    v4_gate_b_config_payload,
)
from bp_engine.v4_research.readiness import assess_v4_gate_b_readiness


class V4PlanIntegrityError(RuntimeError):
    """Raised when the frozen V4 feature-only plan cannot be built safely."""


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat()


def _load_feature_rows(
    connection: Connection,
    *,
    config: V4GateBConfig,
) -> list[Mapping[str, Any]]:
    query = (
        select(
            market_features.c.condition_id,
            market_features.c.slug,
            market_features.c.horizon_seconds,
            market_features.c.market_start_at,
            market_features.c.market_end_at,
            market_features.c.feature_at,
            market_features.c.feature_offset_seconds,
            market_features.c.input_fingerprint,
            market_features.c.feature_hash,
        )
        .where(market_features.c.feature_version == config.feature_version)
        .where(market_features.c.market_start_at >= config.epoch_start)
        .where(market_features.c.market_start_at < config.epoch_end)
    )
    return list(
        connection.execute(
            query.order_by(
                market_features.c.market_start_at,
                market_features.c.condition_id,
                market_features.c.feature_offset_seconds,
                market_features.c.id,
            )
        ).mappings()
    )


def _build_markets(
    rows: list[Mapping[str, Any]],
    *,
    config: V4GateBConfig,
) -> tuple[list[dict[str, Any]], str]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    descriptors: list[dict[str, object]] = []
    for row in rows:
        condition_id = str(row["condition_id"])
        grouped.setdefault(condition_id, []).append(row)
        descriptors.append(
            {
                "condition_id": condition_id,
                "slug": str(row["slug"]),
                "horizon_seconds": int(row["horizon_seconds"]),
                "market_start_at": _utc(row["market_start_at"]),
                "market_end_at": _utc(row["market_end_at"]),
                "feature_at": _utc(row["feature_at"]),
                "feature_offset_seconds": int(row["feature_offset_seconds"]),
                "input_fingerprint": str(row["input_fingerprint"]),
                "feature_hash": str(row["feature_hash"]),
            }
        )

    expected_offsets = set(config.feature_offsets_seconds)
    markets: list[dict[str, Any]] = []
    for condition_id, condition_rows in grouped.items():
        first = condition_rows[0]
        slug = str(first["slug"])
        horizon_seconds = int(first["horizon_seconds"])
        start = _utc(first["market_start_at"])
        end = _utc(first["market_end_at"])
        static_identity = (slug, horizon_seconds, start, end)

        if horizon_seconds != config.horizon_seconds:
            raise V4PlanIntegrityError(
                f"{condition_id} has unexpected horizon {horizon_seconds}"
            )
        if end != start + timedelta(seconds=config.horizon_seconds):
            raise V4PlanIntegrityError(
                f"{condition_id} market bounds do not match frozen horizon"
            )

        offsets: list[int] = []
        for row in condition_rows:
            row_static_identity = (
                str(row["slug"]),
                int(row["horizon_seconds"]),
                _utc(row["market_start_at"]),
                _utc(row["market_end_at"]),
            )
            if row_static_identity != static_identity:
                raise V4PlanIntegrityError(
                    f"{condition_id} has inconsistent static market metadata"
                )
            offset = int(row["feature_offset_seconds"])
            offsets.append(offset)
            if _utc(row["feature_at"]) != start + timedelta(seconds=offset):
                raise V4PlanIntegrityError(
                    f"{condition_id} has feature time inconsistent with offset {offset}"
                )

        if len(offsets) != len(set(offsets)):
            raise V4PlanIntegrityError(
                f"{condition_id} has duplicate feature offsets"
            )
        if set(offsets) != expected_offsets:
            raise V4PlanIntegrityError(
                f"{condition_id} does not have exact frozen feature offsets"
            )
        markets.append(
            {
                "condition_id": condition_id,
                "slug": slug,
                "horizon_seconds": horizon_seconds,
                "market_start_at": start,
                "market_end_at": end,
            }
        )

    markets.sort(key=lambda value: (value["market_start_at"], value["condition_id"]))
    descriptors.sort(
        key=lambda value: (
            value["market_start_at"],
            value["condition_id"],
            value["feature_offset_seconds"],
            value["input_fingerprint"],
            value["feature_hash"],
        )
    )
    return markets, canonical_hash(descriptors)


def _window_members(
    markets: Iterable[Mapping[str, Any]],
    *,
    start: datetime,
    end: datetime,
) -> tuple[list[Mapping[str, Any]], list[str]]:
    contained: list[Mapping[str, Any]] = []
    purged: list[str] = []
    for market in markets:
        market_start = _utc(market["market_start_at"])
        market_end = _utc(market["market_end_at"])
        overlaps = market_start < end and market_end > start
        fully_contained = market_start >= start and market_end <= end
        if fully_contained:
            contained.append(market)
        elif overlaps:
            purged.append(str(market["condition_id"]))
    return contained, purged


def _embargo_earlier_side(
    markets: list[Mapping[str, Any]],
    *,
    embargo_markets: int,
) -> tuple[list[Mapping[str, Any]], list[str]]:
    if embargo_markets == 0:
        return markets, []
    if len(markets) < embargo_markets:
        raise V4PlanIntegrityError("partition is too small for frozen embargo")
    kept = markets[:-embargo_markets]
    removed = [str(market["condition_id"]) for market in markets[-embargo_markets:]]
    return kept, removed


def _condition_ids(markets: Iterable[Mapping[str, Any]]) -> list[str]:
    return [str(market["condition_id"]) for market in markets]


def _partition_payload(
    *,
    start: datetime,
    end: datetime,
    markets: list[Mapping[str, Any]],
) -> dict[str, object]:
    return {
        "start": _iso(start),
        "end": _iso(end),
        "condition_ids": _condition_ids(markets),
    }


def _validate_minimum(
    *,
    label: str,
    markets: list[Mapping[str, Any]],
    minimum: int,
) -> None:
    if len(markets) < minimum:
        raise V4PlanIntegrityError(
            f"{label} requires at least {minimum} markets; found {len(markets)}"
        )


def _build_fold(
    markets: list[dict[str, Any]],
    *,
    index: int,
    start: datetime,
    config: V4GateBConfig,
) -> dict[str, object]:
    train_start = start
    train_end = train_start + config.train_duration
    validation_start = train_end
    validation_end = validation_start + config.validation_duration
    test_start = validation_end
    test_end = test_start + config.test_duration

    train, train_purged = _window_members(markets, start=train_start, end=train_end)
    validation, validation_purged = _window_members(
        markets,
        start=validation_start,
        end=validation_end,
    )
    test, test_purged = _window_members(markets, start=test_start, end=test_end)
    train, train_embargo = _embargo_earlier_side(
        train,
        embargo_markets=config.embargo_markets,
    )
    validation, validation_embargo = _embargo_earlier_side(
        validation,
        embargo_markets=config.embargo_markets,
    )

    _validate_minimum(
        label=f"fold {index} train",
        markets=train,
        minimum=config.min_train_markets,
    )
    _validate_minimum(
        label=f"fold {index} validation",
        markets=validation,
        minimum=config.min_validation_markets,
    )
    _validate_minimum(
        label=f"fold {index} test",
        markets=test,
        minimum=config.min_test_markets,
    )

    payload: dict[str, object] = {
        "index": index,
        "train": _partition_payload(
            start=train_start,
            end=train_end,
            markets=train,
        ),
        "validation": _partition_payload(
            start=validation_start,
            end=validation_end,
            markets=validation,
        ),
        "test": _partition_payload(
            start=test_start,
            end=test_end,
            markets=test,
        ),
        "purged_condition_ids": sorted(
            set(train_purged + validation_purged + test_purged)
        ),
        "embargo_condition_ids": train_embargo + validation_embargo,
    }
    payload["membership_sha256"] = canonical_hash(payload)
    return payload


def _build_final_partition(
    markets: list[dict[str, Any]],
    *,
    config: V4GateBConfig,
) -> dict[str, object]:
    holdout_end = config.epoch_end
    holdout_start = holdout_end - config.final_holdout_duration
    validation_end = holdout_start
    validation_start = validation_end - config.validation_duration
    train_end = validation_start
    train_start = train_end - config.train_duration

    train, train_purged = _window_members(markets, start=train_start, end=train_end)
    validation, validation_purged = _window_members(
        markets,
        start=validation_start,
        end=validation_end,
    )
    holdout, holdout_purged = _window_members(
        markets,
        start=holdout_start,
        end=holdout_end,
    )
    train, train_embargo = _embargo_earlier_side(
        train,
        embargo_markets=config.embargo_markets,
    )
    validation, validation_embargo = _embargo_earlier_side(
        validation,
        embargo_markets=config.embargo_markets,
    )

    _validate_minimum(
        label="final train",
        markets=train,
        minimum=config.min_train_markets,
    )
    _validate_minimum(
        label="final validation",
        markets=validation,
        minimum=config.min_validation_markets,
    )
    _validate_minimum(
        label="final holdout",
        markets=holdout,
        minimum=config.min_final_holdout_markets,
    )

    payload: dict[str, object] = {
        "train": {"start": _iso(train_start), "end": _iso(train_end)},
        "validation": {
            "start": _iso(validation_start),
            "end": _iso(validation_end),
        },
        "holdout": {"start": _iso(holdout_start), "end": _iso(holdout_end)},
        "train_condition_ids": _condition_ids(train),
        "validation_condition_ids": _condition_ids(validation),
        "holdout_condition_ids": _condition_ids(holdout),
        "purged_condition_ids": sorted(
            set(train_purged + validation_purged + holdout_purged)
        ),
        "embargo_condition_ids": train_embargo + validation_embargo,
    }
    payload["membership_sha256"] = canonical_hash(payload)
    return payload


def _assert_partition_integrity(
    *,
    folds: list[dict[str, object]],
    final: Mapping[str, object],
) -> None:
    ordinary_test_ids = [
        condition_id
        for fold in folds
        for condition_id in fold["test"]["condition_ids"]
    ]
    if len(ordinary_test_ids) != len(set(ordinary_test_ids)):
        raise V4PlanIntegrityError("ordinary test membership is not unique")

    final_ids = set(final["holdout_condition_ids"])
    if final_ids.intersection(ordinary_test_ids):
        raise V4PlanIntegrityError(
            "ordinary test membership overlaps final holdout"
        )


def build_v4_gate_b_plan(
    connection: Connection,
    *,
    as_of: datetime,
    config: V4GateBConfig = FROZEN_V4_GATE_B_CONFIG,
) -> dict[str, Any]:
    readiness = assess_v4_gate_b_readiness(
        connection,
        as_of=as_of,
        config=config,
    )
    if not readiness["ready"]:
        reasons = ",".join(readiness["blocking_reasons"])
        raise V4PlanIntegrityError(f"readiness failed: {reasons}")

    rows = _load_feature_rows(connection, config=config)
    markets, feature_manifest_sha256 = _build_markets(rows, config=config)
    if not markets:
        raise V4PlanIntegrityError("no eligible V4 markets in frozen epoch")

    folds = [
        _build_fold(
            markets,
            index=index,
            start=config.epoch_start + index * config.step_duration,
            config=config,
        )
        for index in range(config.ordinary_fold_count)
    ]
    final = _build_final_partition(markets, config=config)
    _assert_partition_integrity(folds=folds, final=final)

    payload: dict[str, Any] = {
        "research_plan_version": config.research_plan_version,
        "dataset_version": config.dataset_version,
        "feature_version": config.feature_version,
        "label_version": config.label_version,
        "horizon_seconds": config.horizon_seconds,
        "feature_offsets_seconds": list(config.feature_offsets_seconds),
        "epoch_start": _iso(config.epoch_start),
        "epoch_end": _iso(config.epoch_end),
        "market_count": len(markets),
        "known_regime_market_counts": readiness["regime_market_counts"],
        "readiness_input_sha256": readiness["readiness_input_sha256"],
        "config_sha256": canonical_hash(v4_gate_b_config_payload(config)),
        "feature_manifest_sha256": feature_manifest_sha256,
        "folds": folds,
        "final": final,
        "labels_read": False,
        "training_performed": False,
        "policy_selected": False,
        "final_holdout_evaluated": False,
    }
    payload["plan_sha256"] = canonical_hash(payload)
    return payload
