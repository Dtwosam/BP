from __future__ import annotations

import importlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from bp_engine.calibration.models import EdgeConfig
from bp_engine.calibration.source import FinalSourceSpec, SourceFoldSpec
from bp_engine.modeling.models import DatasetSnapshot, SupervisedRow

START = datetime(2026, 8, 24, tzinfo=UTC)


def _module():
    return importlib.import_module("bp_engine.calibration.evaluation")


def _row(condition: str, probability: float | None, target: int, offset: int) -> SupervisedRow:
    index = int(condition.split("-")[-1])
    start = START + timedelta(minutes=5 * index)
    predictors = {
        "pm_up_price": probability,
        "pm_up_best_ask": 0.55,
        "pm_up_best_bid": 0.54,
        "pm_down_best_ask": 0.55,
        "pm_down_best_bid": 0.54,
        "missing__pm_up_book_missing": 0.0,
        "missing__pm_up_book_stale": 0.0,
        "missing__pm_down_book_missing": 0.0,
        "missing__pm_down_book_stale": 0.0,
    }
    return SupervisedRow(
        condition_id=condition,
        slug=f"slug-{condition}",
        horizon_seconds=300,
        market_start_at=start,
        market_end_at=start + timedelta(minutes=5),
        feature_at=start + timedelta(seconds=offset),
        feature_offset_seconds=offset,
        predictors=predictors,
        target=target,
        feature_hash=f"feature-{condition}-{offset}",
        input_fingerprint=f"input-{condition}-{offset}",
    )


def _dataset(
    *,
    flip_test: bool = False,
    flip_validation: bool = False,
    missing_test_price: bool = False,
) -> DatasetSnapshot:
    train = [
        ("train-0", 0.20, 0),
        ("train-1", 0.25, 0),
        ("train-2", 0.35, 0),
        ("train-3", 0.40, 0),
        ("train-4", 0.60, 1),
        ("train-5", 0.65, 1),
        ("train-6", 0.75, 1),
        ("train-7", 0.80, 1),
    ]
    validation = [
        ("val-8", 0.80, 1),
        ("val-9", 0.75, 1),
        ("val-10", 0.20, 0),
        ("val-11", 0.25, 0),
    ]
    test = [
        ("test-12", None if missing_test_price else 0.80, 1),
        ("test-13", 0.20, 0),
        ("test-14", 0.75, 1),
        ("test-15", 0.25, 0),
    ]
    if flip_validation:
        validation = [
            (condition, probability, 1 - target)
            for condition, probability, target in validation
        ]
    if flip_test:
        test = [
            (condition, probability, 1 - target)
            for condition, probability, target in test
        ]

    rows: list[SupervisedRow] = []
    for condition, probability, target in [*train, *validation, *test]:
        rows.append(_row(condition, probability, target, 120))
        rows.append(_row(condition, 0.51 if probability is not None else None, target, 60))
    return DatasetSnapshot(
        dataset_version="supervised-core-v1",
        feature_version="core-v1",
        label_version="official-outcome-v1",
        horizon_seconds=300,
        start=START,
        end=START + timedelta(days=1),
        rows=tuple(rows),
        predictor_names=("pm_up_price",),
        dataset_sha256="d" * 64,
    )


def _fold() -> SourceFoldSpec:
    return SourceFoldSpec(
        index=0,
        membership_sha256="e" * 64,
        train_condition_ids=tuple(f"train-{index}" for index in range(8)),
        validation_condition_ids=tuple(f"val-{index}" for index in range(8, 12)),
        test_condition_ids=tuple(f"test-{index}" for index in range(12, 16)),
        selected_offset_seconds=120,
    )


def _final() -> FinalSourceSpec:
    fold = _fold()
    return FinalSourceSpec(
        membership_sha256="f" * 64,
        train_condition_ids=fold.train_condition_ids,
        validation_condition_ids=fold.validation_condition_ids,
        holdout_condition_ids=fold.test_condition_ids,
        selected_offset_seconds=120,
    )


def _config() -> EdgeConfig:
    return EdgeConfig(
        fee_rate=0.0,
        slippage_buffer=0.0,
        min_edge_grid=(0.0, 0.05, 0.10),
        min_validation_trades=2,
        max_spread=None,
    )


def test_fold_reuses_frozen_offset_and_selection_boundaries() -> None:
    report = _module().evaluate_source_fold(_dataset(), _fold(), _config())

    assert report.selected_offset_seconds == 120
    assert report.calibration_selection_fit_partition == "train"
    assert report.calibration_selection_partition == "validation"
    assert report.edge_selection_partition == "validation"
    assert report.prediction_coverage == 1.0
    assert report.edge_policy_selection.policy == "trade_threshold"


def test_test_labels_cannot_rewrite_calibrator_or_edge_threshold() -> None:
    module = _module()
    left = module.evaluate_source_fold(_dataset(), _fold(), _config())
    right = module.evaluate_source_fold(_dataset(flip_test=True), _fold(), _config())

    assert left.calibration_selection.method == right.calibration_selection.method
    assert left.calibration_selection.fit == right.calibration_selection.fit
    assert left.edge_policy_selection.policy == right.edge_policy_selection.policy
    assert left.edge_policy_selection.min_edge == right.edge_policy_selection.min_edge
    assert left.calibrated_metrics.accuracy != right.calibrated_metrics.accuracy


def test_validation_labels_can_change_frozen_policy() -> None:
    module = _module()
    profitable = module.evaluate_source_fold(_dataset(), _fold(), _config())
    losing = module.evaluate_source_fold(
        _dataset(flip_validation=True), _fold(), _config()
    )

    assert profitable.edge_policy_selection.policy == "trade_threshold"
    assert losing.edge_policy_selection.policy == "no_trade"


def test_missing_market_price_counts_prediction_but_not_trade_eligibility() -> None:
    report = _module().evaluate_source_fold(
        _dataset(missing_test_price=True), _fold(), _config()
    )

    assert report.predicted_test_markets == 4
    assert report.prediction_coverage == 1.0
    assert report.observed_market_price_test_markets == 3
    assert report.observed_market_price_coverage == 0.75
    assert report.edge_metrics.market_probability_observed_markets == 3
    assert report.edge_metrics.reason_counts["missing_market_probability"] == 1


def test_final_evaluation_uses_same_train_validation_holdout_boundaries() -> None:
    report = _module().evaluate_final_source(_dataset(), _final(), _config())

    assert report.selected_offset_seconds == 120
    assert report.calibration_selection_fit_partition == "train"
    assert report.calibration_selection_partition == "validation"
    assert report.edge_selection_partition == "validation"
    assert report.evaluation_partition == "holdout"


def test_missing_condition_at_frozen_offset_reduces_prediction_coverage() -> None:
    dataset = _dataset()
    rows = tuple(
        row
        for row in dataset.rows
        if not (row.condition_id == "test-15" and row.feature_offset_seconds == 120)
    )
    report = _module().evaluate_source_fold(
        replace(dataset, rows=rows), _fold(), _config()
    )

    assert report.expected_test_markets == 4
    assert report.predicted_test_markets == 3
    assert report.missing_offset_condition_ids == ("test-15",)
    assert report.prediction_coverage == 0.75
