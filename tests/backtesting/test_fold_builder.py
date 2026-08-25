from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from bp_engine.backtesting.folds import (
    FoldEligibilityError,
    build_market_timeline,
    build_walk_forward_plan,
)
from bp_engine.backtesting.models import WalkForwardConfig

from bp_engine.modeling.models import DatasetSnapshot, SupervisedRow


def _row(
    *,
    condition_id: str,
    start: datetime,
    horizon_seconds: int = 900,
    offset_seconds: int = 60,
    target: int = 0,
) -> SupervisedRow:
    return SupervisedRow(
        condition_id=condition_id,
        slug=f"btc-updown-{horizon_seconds}-{condition_id}",
        horizon_seconds=horizon_seconds,
        market_start_at=start,
        market_end_at=start + timedelta(seconds=horizon_seconds),
        feature_at=start + timedelta(seconds=offset_seconds),
        feature_offset_seconds=offset_seconds,
        predictors={"pm_up_price": 0.5},
        target=target,
        feature_hash="a" * 64,
        input_fingerprint="b" * 64,
    )


def _dataset(*, markets: int = 96, horizon_seconds: int = 900) -> DatasetSnapshot:
    start = datetime(2026, 8, 24, tzinfo=UTC)
    rows: list[SupervisedRow] = []
    for index in range(markets):
        market_start = start + timedelta(seconds=index * horizon_seconds)
        for offset in (60, 120):
            rows.append(
                _row(
                    condition_id=f"condition-{index:03d}",
                    start=market_start,
                    horizon_seconds=horizon_seconds,
                    offset_seconds=offset,
                    target=index % 2,
                )
            )
    return DatasetSnapshot(
        dataset_version="supervised-core-v1",
        feature_version="core-v1",
        label_version="official-outcome-v1",
        horizon_seconds=horizon_seconds,
        start=start,
        end=start + timedelta(seconds=markets * horizon_seconds),
        rows=tuple(rows),
        predictor_names=("pm_up_price",),
        dataset_sha256="c" * 64,
    )


def _config(**overrides: object) -> WalkForwardConfig:
    values: dict[str, object] = {
        "train_duration": timedelta(hours=8),
        "validation_duration": timedelta(hours=2),
        "test_duration": timedelta(hours=2),
        "step_duration": timedelta(hours=2),
        "final_holdout_duration": timedelta(hours=2),
        "embargo_markets": 1,
        "min_train_markets": 24,
        "min_validation_markets": 6,
        "min_test_markets": 6,
        "min_market_price_coverage": 0.80,
        "min_prediction_coverage": 0.90,
    }
    values.update(overrides)
    return WalkForwardConfig(**values)  # type: ignore[arg-type]


def test_market_timeline_groups_feature_rows_by_condition() -> None:
    dataset = _dataset(markets=4)

    timeline = build_market_timeline(dataset)

    assert len(timeline) == 4
    assert timeline[0].condition_id == "condition-000"
    assert timeline[-1].condition_id == "condition-003"


def test_market_timeline_rejects_static_metadata_mismatch() -> None:
    dataset = _dataset(markets=2)
    first = dataset.rows[0]
    bad = SupervisedRow(
        **{
            **first.__dict__,
            "slug": "conflicting-slug",
            "feature_at": first.market_start_at + timedelta(seconds=180),
            "feature_offset_seconds": 180,
        }
    )
    changed = DatasetSnapshot(
        **{**dataset.__dict__, "rows": (*dataset.rows, bad)}
    )

    with pytest.raises(ValueError, match="static market metadata"):
        build_market_timeline(changed)


def test_walk_forward_config_rejects_overlapping_test_steps() -> None:
    with pytest.raises(ValueError, match="step_duration must be at least test_duration"):
        _config(step_duration=timedelta(hours=1))


def test_walk_forward_plan_is_chronological_disjoint_and_reserves_final_holdout() -> None:
    dataset = _dataset()

    plan = build_walk_forward_plan(dataset, _config())

    assert len(plan.folds) >= 3
    assert plan.final_holdout.start == dataset.end - timedelta(hours=2)
    assert plan.final_holdout.end == dataset.end
    ordinary_tests: set[str] = set()
    for fold in plan.folds:
        train = set(fold.train.condition_ids)
        validation = set(fold.validation.condition_ids)
        test = set(fold.test.condition_ids)
        assert train.isdisjoint(validation)
        assert train.isdisjoint(test)
        assert validation.isdisjoint(test)
        assert ordinary_tests.isdisjoint(test)
        ordinary_tests |= test
        assert len(fold.embargo_condition_ids) >= 2
    assert ordinary_tests.isdisjoint(plan.final_holdout.condition_ids)


def test_boundary_crossing_market_is_purged_not_partially_assigned() -> None:
    dataset = _dataset()
    boundary = dataset.start + timedelta(hours=8)
    crossing = _row(
        condition_id="crossing",
        start=boundary - timedelta(minutes=5),
        horizon_seconds=900,
        target=1,
    )
    changed = DatasetSnapshot(
        **{**dataset.__dict__, "rows": (*dataset.rows, crossing)}
    )

    plan = build_walk_forward_plan(changed, _config())

    first = plan.folds[0]
    assert "crossing" not in first.train.condition_ids
    assert "crossing" not in first.validation.condition_ids
    assert "crossing" in first.purged_condition_ids


def test_fold_builder_fails_when_partition_has_one_class() -> None:
    dataset = _dataset()
    rows = tuple(
        SupervisedRow(**{**row.__dict__, "target": 1})
        for row in dataset.rows
    )
    one_class = DatasetSnapshot(**{**dataset.__dict__, "rows": rows})

    with pytest.raises(FoldEligibilityError, match="both classes"):
        build_walk_forward_plan(one_class, _config())
