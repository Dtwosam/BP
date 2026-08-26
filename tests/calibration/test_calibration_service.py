from __future__ import annotations

import importlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from bp_engine.calibration.models import EdgeConfig
from bp_engine.calibration.source import BacktestSourceSpec, FinalSourceSpec, SourceFoldSpec
from bp_engine.modeling.models import DatasetSnapshot, SupervisedRow

START = datetime(2026, 8, 24, tzinfo=UTC)
END = datetime(2026, 8, 25, tzinfo=UTC)


def _module():
    return importlib.import_module("bp_engine.calibration.service")


def _row(index: int, offset: int) -> SupervisedRow:
    target = index % 2
    probability = 0.80 if target else 0.20
    start = START + timedelta(minutes=5 * index)
    predictors = {
        "pm_up_price": probability if offset == 120 else 0.51,
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
        condition_id=f"condition-{index}",
        slug=f"slug-{index}",
        horizon_seconds=300,
        market_start_at=start,
        market_end_at=start + timedelta(minutes=5),
        feature_at=start + timedelta(seconds=offset),
        feature_offset_seconds=offset,
        predictors=predictors,
        target=target,
        feature_hash=f"feature-{index}-{offset}",
        input_fingerprint=f"input-{index}-{offset}",
    )


def _dataset() -> DatasetSnapshot:
    rows = tuple(_row(index, offset) for index in range(24) for offset in (60, 120))
    return DatasetSnapshot(
        dataset_version="supervised-core-v1",
        feature_version="core-v1",
        label_version="official-outcome-v1",
        horizon_seconds=300,
        start=START,
        end=END,
        rows=rows,
        predictor_names=("pm_up_price",),
        dataset_sha256="b" * 64,
    )


def _ids(start: int, end: int) -> tuple[str, ...]:
    return tuple(f"condition-{index}" for index in range(start, end))


def _source() -> BacktestSourceSpec:
    first = SourceFoldSpec(
        index=0,
        membership_sha256="e" * 64,
        train_condition_ids=_ids(0, 8),
        validation_condition_ids=_ids(8, 12),
        test_condition_ids=_ids(12, 16),
        selected_offset_seconds=120,
    )
    second = SourceFoldSpec(
        index=1,
        membership_sha256="7" * 64,
        train_condition_ids=_ids(0, 8),
        validation_condition_ids=_ids(8, 12),
        test_condition_ids=_ids(16, 20),
        selected_offset_seconds=120,
    )
    final = FinalSourceSpec(
        membership_sha256="f" * 64,
        train_condition_ids=_ids(0, 8),
        validation_condition_ids=_ids(8, 12),
        holdout_condition_ids=_ids(20, 24),
        selected_offset_seconds=120,
    )
    return BacktestSourceSpec(
        run_id="phase8-300-source",
        backtest_version="walk-forward-v1",
        semantic_sha256="9" * 64,
        source_training_run_id="phase7-300-source",
        source_training_semantic_sha256="a" * 64,
        dataset_version="supervised-core-v1",
        feature_version="core-v1",
        label_version="official-outcome-v1",
        horizon_seconds=300,
        start=START,
        end=END,
        dataset_sha256="b" * 64,
        config_sha256="c" * 64,
        plan_sha256="d" * 64,
        fold_membership_sha256=("e" * 64, "7" * 64, "f" * 64),
        folds=(first, second),
        final=final,
    )


def _config() -> EdgeConfig:
    return EdgeConfig(
        fee_rate=0.07,
        slippage_buffer=0.01,
        min_edge_grid=(0.0, 0.01, 0.02, 0.03, 0.05, 0.075, 0.10, 0.15),
        min_validation_trades=3,
        max_spread=None,
    )


def _patch(monkeypatch, module, source=None, dataset=None) -> None:
    selected_source = source or _source()
    selected_dataset = dataset or _dataset()
    monkeypatch.setattr(
        module,
        "load_backtest_source_spec",
        lambda connection, run_id: selected_source,
    )
    monkeypatch.setattr(
        module,
        "load_dataset",
        lambda connection, **kwargs: selected_dataset,
    )


def test_report_semantics_and_run_id_are_deterministic(monkeypatch) -> None:
    module = _module()
    _patch(monkeypatch, module)
    t0 = datetime(2026, 8, 26, tzinfo=UTC)
    t1 = t0 + timedelta(hours=1)

    left = module.run_calibration_edge_analysis(
        object(),
        source_backtest_run_id="phase8-300-source",
        start=START,
        end=END,
        edge_config=_config(),
        created_at=t0,
    )
    right = module.run_calibration_edge_analysis(
        object(),
        source_backtest_run_id="phase8-300-source",
        start=START,
        end=END,
        edge_config=_config(),
        created_at=t1,
    )

    assert left.semantic_sha256 == right.semantic_sha256
    assert left.run_id == right.run_id
    assert left.run_id == f"phase9-300-{left.semantic_sha256[:32]}"
    assert left.created_at != right.created_at
    assert left.config_sha256 == right.config_sha256
    assert left.config["fee_rate"] == 0.07
    assert left.config["slippage_buffer"] == 0.01


def test_aggregate_oos_is_sum_of_frozen_fold_decisions(monkeypatch) -> None:
    module = _module()
    _patch(monkeypatch, module)

    report = module.run_calibration_edge_analysis(
        object(),
        source_backtest_run_id="phase8-300-source",
        start=START,
        end=END,
        edge_config=_config(),
        created_at=datetime(2026, 8, 26, tzinfo=UTC),
    )

    expected_trades = sum(fold.edge_metrics.trade_count for fold in report.folds)
    expected_pnl = sum(
        fold.edge_metrics.realized_pnl_after_assumed_costs for fold in report.folds
    )
    assert report.aggregate_oos.edge_metrics.trade_count == expected_trades
    assert report.aggregate_oos.edge_metrics.realized_pnl_after_assumed_costs == pytest.approx(
        expected_pnl
    )
    assert len(report.aggregate_oos.condition_ids) == 8
    assert not hasattr(report.aggregate_oos, "selected_min_edge")


def test_dataset_sha_mismatch_fails_closed(monkeypatch) -> None:
    module = _module()
    _patch(monkeypatch, module, dataset=replace(_dataset(), dataset_sha256="0" * 64))

    with pytest.raises(module.CalibrationEdgeIntegrityError, match="dataset_sha256"):
        module.run_calibration_edge_analysis(
            object(),
            source_backtest_run_id="phase8-300-source",
            start=START,
            end=END,
            edge_config=_config(),
            created_at=datetime(2026, 8, 26, tzinfo=UTC),
        )


def test_requested_window_must_equal_source_window(monkeypatch) -> None:
    module = _module()
    _patch(monkeypatch, module)

    with pytest.raises(module.CalibrationEdgeIntegrityError, match="source window"):
        module.run_calibration_edge_analysis(
            object(),
            source_backtest_run_id="phase8-300-source",
            start=START + timedelta(seconds=1),
            end=END,
            edge_config=_config(),
            created_at=datetime(2026, 8, 26, tzinfo=UTC),
        )


def test_duplicate_ordinary_oos_condition_is_rejected(monkeypatch) -> None:
    module = _module()
    source = _source()
    duplicate = replace(
        source.folds[1],
        test_condition_ids=(source.folds[0].test_condition_ids[0], *_ids(17, 20)),
    )
    _patch(monkeypatch, module, source=replace(source, folds=(source.folds[0], duplicate)))

    with pytest.raises(
        module.CalibrationEdgeIntegrityError, match="ordinary OOS market reused"
    ):
        module.run_calibration_edge_analysis(
            object(),
            source_backtest_run_id="phase8-300-source",
            start=START,
            end=END,
            edge_config=_config(),
            created_at=datetime(2026, 8, 26, tzinfo=UTC),
        )


def test_final_holdout_overlap_is_rejected_before_evaluation(monkeypatch) -> None:
    module = _module()
    source = _source()
    overlapping_final = replace(
        source.final,
        holdout_condition_ids=(source.folds[0].test_condition_ids[0], *_ids(21, 24)),
    )
    _patch(monkeypatch, module, source=replace(source, final=overlapping_final))

    with pytest.raises(
        module.CalibrationEdgeIntegrityError, match="final holdout overlaps"
    ):
        module.run_calibration_edge_analysis(
            object(),
            source_backtest_run_id="phase8-300-source",
            start=START,
            end=END,
            edge_config=_config(),
            created_at=datetime(2026, 8, 26, tzinfo=UTC),
        )
