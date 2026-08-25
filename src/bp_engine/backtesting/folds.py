from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from bp_engine.backtesting.models import (
    FoldPartition,
    MarketRecord,
    WalkForwardConfig,
    WalkForwardFold,
    WalkForwardPlan,
)
from bp_engine.features.hashing import canonical_hash
from bp_engine.modeling.models import DatasetSnapshot, SupervisedRow


class FoldEligibilityError(ValueError):
    """Raised when a requested walk-forward partition is not evaluable."""


def _require_aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def build_market_timeline(dataset: DatasetSnapshot) -> tuple[MarketRecord, ...]:
    grouped: dict[str, list[SupervisedRow]] = defaultdict(list)
    for row in dataset.rows:
        grouped[row.condition_id].append(row)

    timeline: list[MarketRecord] = []
    for condition_id, rows in grouped.items():
        first = rows[0]
        _require_aware("market_start_at", first.market_start_at)
        _require_aware("market_end_at", first.market_end_at)
        expected = (
            first.slug,
            first.horizon_seconds,
            first.market_start_at,
            first.market_end_at,
            first.target,
        )
        for row in rows[1:]:
            observed = (
                row.slug,
                row.horizon_seconds,
                row.market_start_at,
                row.market_end_at,
                row.target,
            )
            if observed != expected:
                raise ValueError(
                    f"static market metadata mismatch for condition_id={condition_id}"
                )
        if first.horizon_seconds != dataset.horizon_seconds:
            raise ValueError(
                f"static market metadata mismatch for condition_id={condition_id}"
            )
        if first.market_end_at <= first.market_start_at:
            raise ValueError("market_end_at must be after market_start_at")
        if first.target not in (0, 1):
            raise ValueError("target must be binary")
        timeline.append(
            MarketRecord(
                condition_id=condition_id,
                slug=first.slug,
                horizon_seconds=first.horizon_seconds,
                market_start_at=first.market_start_at,
                market_end_at=first.market_end_at,
                target=first.target,
            )
        )

    return tuple(
        sorted(timeline, key=lambda item: (item.market_start_at, item.condition_id))
    )


def _contained(
    timeline: tuple[MarketRecord, ...], start: datetime, end: datetime
) -> tuple[MarketRecord, ...]:
    return tuple(
        market
        for market in timeline
        if market.market_start_at >= start and market.market_end_at <= end
    )


def _crosses_boundary(market: MarketRecord, boundary: datetime) -> bool:
    return market.market_start_at < boundary < market.market_end_at


def _purged_at_boundaries(
    timeline: tuple[MarketRecord, ...], boundaries: tuple[datetime, ...]
) -> tuple[str, ...]:
    return tuple(
        market.condition_id
        for market in timeline
        if any(_crosses_boundary(market, boundary) for boundary in boundaries)
    )


def _embargo_earlier_partition(
    records: tuple[MarketRecord, ...], count: int
) -> tuple[tuple[MarketRecord, ...], tuple[str, ...]]:
    if count == 0:
        return records, ()
    removed = records[-count:]
    kept = records[:-count]
    return kept, tuple(market.condition_id for market in removed)


def _partition(
    name: str, start: datetime, end: datetime, records: tuple[MarketRecord, ...]
) -> FoldPartition:
    return FoldPartition(
        name=name,
        start=start,
        end=end,
        condition_ids=tuple(market.condition_id for market in records),
    )


def _validate_partition(
    partition: FoldPartition,
    records_by_id: dict[str, MarketRecord],
    *,
    minimum: int,
) -> None:
    if len(partition.condition_ids) < minimum:
        raise FoldEligibilityError(
            f"{partition.name} requires at least {minimum} markets; "
            f"found {len(partition.condition_ids)}"
        )
    classes = {records_by_id[condition_id].target for condition_id in partition.condition_ids}
    if classes != {0, 1}:
        raise FoldEligibilityError(f"{partition.name} must contain both classes")


def _membership_hash(
    *,
    index: int,
    train: FoldPartition,
    validation: FoldPartition,
    test: FoldPartition,
    purged: tuple[str, ...],
    embargo: tuple[str, ...],
) -> str:
    return canonical_hash(
        {
            "index": index,
            "train": {
                "start": train.start,
                "end": train.end,
                "condition_ids": train.condition_ids,
            },
            "validation": {
                "start": validation.start,
                "end": validation.end,
                "condition_ids": validation.condition_ids,
            },
            "test": {
                "start": test.start,
                "end": test.end,
                "condition_ids": test.condition_ids,
            },
            "purged_condition_ids": purged,
            "embargo_condition_ids": embargo,
        }
    )


def _config_payload(config: WalkForwardConfig) -> dict[str, float | int]:
    return {
        "train_duration_seconds": config.train_duration.total_seconds(),
        "validation_duration_seconds": config.validation_duration.total_seconds(),
        "test_duration_seconds": config.test_duration.total_seconds(),
        "step_duration_seconds": config.step_duration.total_seconds(),
        "final_holdout_duration_seconds": config.final_holdout_duration.total_seconds(),
        "embargo_markets": config.embargo_markets,
        "min_train_markets": config.min_train_markets,
        "min_validation_markets": config.min_validation_markets,
        "min_test_markets": config.min_test_markets,
        "min_market_price_coverage": config.min_market_price_coverage,
        "min_prediction_coverage": config.min_prediction_coverage,
    }


def build_walk_forward_plan(
    dataset: DatasetSnapshot, config: WalkForwardConfig
) -> WalkForwardPlan:
    _require_aware("dataset.start", dataset.start)
    _require_aware("dataset.end", dataset.end)
    if dataset.end <= dataset.start:
        raise ValueError("dataset.start must be before dataset.end")

    timeline = build_market_timeline(dataset)
    records_by_id = {market.condition_id: market for market in timeline}
    holdout_start = dataset.end - config.final_holdout_duration
    if holdout_start <= dataset.start:
        raise FoldEligibilityError("final holdout leaves no walk-forward history")

    folds: list[WalkForwardFold] = []
    fold_start = dataset.start
    index = 0
    while True:
        train_start = fold_start
        train_end = train_start + config.train_duration
        validation_start = train_end
        validation_end = validation_start + config.validation_duration
        test_start = validation_end
        test_end = test_start + config.test_duration
        if test_end > holdout_start:
            break

        train_records = _contained(timeline, train_start, train_end)
        validation_records = _contained(timeline, validation_start, validation_end)
        test_records = _contained(timeline, test_start, test_end)
        purged = _purged_at_boundaries(
            timeline, (train_start, train_end, validation_end, test_end)
        )
        train_records, train_embargo = _embargo_earlier_partition(
            train_records, config.embargo_markets
        )
        validation_records, validation_embargo = _embargo_earlier_partition(
            validation_records, config.embargo_markets
        )
        embargo = train_embargo + validation_embargo

        train = _partition("train", train_start, train_end, train_records)
        validation = _partition(
            "validation", validation_start, validation_end, validation_records
        )
        test = _partition("test", test_start, test_end, test_records)
        _validate_partition(train, records_by_id, minimum=config.min_train_markets)
        _validate_partition(
            validation, records_by_id, minimum=config.min_validation_markets
        )
        _validate_partition(test, records_by_id, minimum=config.min_test_markets)

        folds.append(
            WalkForwardFold(
                index=index,
                train=train,
                validation=validation,
                test=test,
                purged_condition_ids=purged,
                embargo_condition_ids=embargo,
                membership_sha256=_membership_hash(
                    index=index,
                    train=train,
                    validation=validation,
                    test=test,
                    purged=purged,
                    embargo=embargo,
                ),
            )
        )
        index += 1
        fold_start = fold_start + config.step_duration

    if len(folds) < 3:
        raise FoldEligibilityError(
            f"walk-forward plan requires at least 3 eligible folds; found {len(folds)}"
        )

    final_holdout_start = holdout_start
    final_validation_end = final_holdout_start
    final_validation_start = final_validation_end - config.validation_duration
    final_train_end = final_validation_start
    final_train_start = final_train_end - config.train_duration
    if final_train_start < dataset.start:
        raise FoldEligibilityError("final train window begins before dataset start")

    final_train_records = _contained(timeline, final_train_start, final_train_end)
    final_validation_records = _contained(
        timeline, final_validation_start, final_validation_end
    )
    final_holdout_records = _contained(timeline, final_holdout_start, dataset.end)
    final_purged = _purged_at_boundaries(
        timeline,
        (
            final_train_start,
            final_train_end,
            final_validation_end,
            dataset.end,
        ),
    )
    final_train_records, final_train_embargo = _embargo_earlier_partition(
        final_train_records, config.embargo_markets
    )
    final_validation_records, final_validation_embargo = _embargo_earlier_partition(
        final_validation_records, config.embargo_markets
    )
    final_embargo = final_train_embargo + final_validation_embargo

    final_train = _partition(
        "final_train", final_train_start, final_train_end, final_train_records
    )
    final_validation = _partition(
        "final_validation",
        final_validation_start,
        final_validation_end,
        final_validation_records,
    )
    final_holdout = _partition(
        "final_holdout", final_holdout_start, dataset.end, final_holdout_records
    )
    _validate_partition(final_train, records_by_id, minimum=config.min_train_markets)
    _validate_partition(
        final_validation, records_by_id, minimum=config.min_validation_markets
    )
    _validate_partition(final_holdout, records_by_id, minimum=config.min_test_markets)

    final_membership_sha = canonical_hash(
        {
            "train": final_train.condition_ids,
            "validation": final_validation.condition_ids,
            "holdout": final_holdout.condition_ids,
            "purged": final_purged,
            "embargo": final_embargo,
        }
    )
    plan_sha256 = canonical_hash(
        {
            "dataset_sha256": dataset.dataset_sha256,
            "config": _config_payload(config),
            "folds": tuple(fold.membership_sha256 for fold in folds),
            "final_membership_sha256": final_membership_sha,
        }
    )
    return WalkForwardPlan(
        folds=tuple(folds),
        final_train=final_train,
        final_validation=final_validation,
        final_holdout=final_holdout,
        final_purged_condition_ids=final_purged,
        final_embargo_condition_ids=final_embargo,
        plan_sha256=plan_sha256,
    )
