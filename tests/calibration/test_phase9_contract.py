from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, func, select

from bp_engine.calibration.evaluation import evaluate_final_source, evaluate_source_fold
from bp_engine.calibration.models import EdgeConfig
from bp_engine.calibration.repository import CalibrationEdgeRunRepository
from bp_engine.calibration.service import run_calibration_edge_analysis
from bp_engine.calibration.source import BacktestSourceSpec, FinalSourceSpec, SourceFoldSpec
from bp_engine.features.hashing import canonical_hash
from bp_engine.modeling.models import DatasetSnapshot, SupervisedRow
from bp_engine.storage.schema import calibration_edge_runs, metadata

START = datetime(2026, 8, 24, tzinfo=UTC)
END = datetime(2026, 8, 25, tzinfo=UTC)


def _row(
    index: int,
    *,
    offset_seconds: int,
    target: int,
    probability: float,
) -> SupervisedRow:
    market_start = START + timedelta(minutes=5 * index)
    side_probability = probability if target == 1 else 1.0 - probability
    ask = 0.55 if side_probability >= 0.5 else 0.70
    predictors = {
        "pm_up_price": probability,
        "pm_up_best_ask": ask if probability >= 0.5 else 0.70,
        "pm_up_best_bid": (ask - 0.01) if probability >= 0.5 else 0.69,
        "pm_down_best_ask": ask if probability < 0.5 else 0.70,
        "pm_down_best_bid": (ask - 0.01) if probability < 0.5 else 0.69,
        "missing__pm_up_book_missing": 0.0,
        "missing__pm_up_book_stale": 0.0,
        "missing__pm_down_book_missing": 0.0,
        "missing__pm_down_book_stale": 0.0,
    }
    return SupervisedRow(
        condition_id=f"condition-{index}",
        slug=f"slug-{index}",
        horizon_seconds=300,
        market_start_at=market_start,
        market_end_at=market_start + timedelta(minutes=5),
        feature_at=market_start + timedelta(seconds=offset_seconds),
        feature_offset_seconds=offset_seconds,
        predictors=predictors,
        target=target,
        feature_hash=f"feature-{index}-{offset_seconds}-{target}",
        input_fingerprint=f"input-{index}-{offset_seconds}-{target}",
    )


def _target(index: int) -> int:
    return index % 2


def _probability(index: int, offset_seconds: int) -> float:
    target = _target(index)
    if offset_seconds == 120:
        return 0.80 if target else 0.20
    return 0.76 if target else 0.24


def _dataset(
    *,
    flip_validation: bool = False,
    flip_ordinary_test: bool = False,
    flip_final_holdout: bool = False,
) -> DatasetSnapshot:
    rows: list[SupervisedRow] = []
    for index in range(24):
        target = _target(index)
        if flip_validation and 8 <= index < 12:
            target = 1 - target
        if flip_ordinary_test and 12 <= index < 20:
            target = 1 - target
        if flip_final_holdout and 20 <= index < 24:
            target = 1 - target
        for offset_seconds in (60, 120):
            rows.append(
                _row(
                    index,
                    offset_seconds=offset_seconds,
                    target=target,
                    probability=_probability(index, offset_seconds),
                )
            )
    return DatasetSnapshot(
        dataset_version="supervised-core-v1",
        feature_version="core-v1",
        label_version="official-outcome-v1",
        horizon_seconds=300,
        start=START,
        end=END,
        rows=tuple(rows),
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
        selected_offset_seconds=60,
    )
    final = FinalSourceSpec(
        membership_sha256="f" * 64,
        train_condition_ids=_ids(0, 8),
        validation_condition_ids=_ids(8, 12),
        holdout_condition_ids=_ids(20, 24),
        selected_offset_seconds=120,
    )
    return BacktestSourceSpec(
        run_id="phase8-300-contract",
        backtest_version="walk-forward-v1",
        semantic_sha256="9" * 64,
        source_training_run_id="phase7-300-contract",
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


def _selection_hash(report: object) -> str:
    return canonical_hash(
        {
            "selected_offset_seconds": report.selected_offset_seconds,
            "calibration_fit": asdict(report.calibration_selection.fit),
            "edge_policy": report.edge_policy_selection.policy,
            "min_edge": report.edge_policy_selection.min_edge,
        }
    )


def test_spectacular_test_outcomes_cannot_rescue_losing_validation_economics() -> None:
    source = _source().folds[0]
    losing_validation = _dataset(flip_validation=True)
    report = evaluate_source_fold(losing_validation, source, _config())
    spectacular_test = evaluate_source_fold(
        replace(losing_validation, rows=_dataset(flip_validation=True).rows),
        source,
        _config(),
    )

    assert report.edge_policy_selection.policy == "no_trade"
    assert report.edge_policy_selection.min_edge is None
    assert spectacular_test.edge_policy_selection == report.edge_policy_selection
    assert report.edge_metrics.trade_count == 0
    assert report.calibration_selection_fit_partition == "train"
    assert report.calibration_selection_partition == "validation"
    assert report.edge_selection_partition == "validation"
    assert report.evaluation_partition == "test"


def test_ordinary_test_target_mutation_cannot_rewrite_frozen_selection() -> None:
    source = _source().folds[0]
    left = evaluate_source_fold(_dataset(), source, _config())
    right = evaluate_source_fold(
        _dataset(flip_ordinary_test=True), source, _config()
    )

    assert _selection_hash(left) == _selection_hash(right)
    assert left.edge_policy_selection == right.edge_policy_selection
    assert left.calibrated_metrics is not None
    assert right.calibrated_metrics is not None
    assert left.calibrated_metrics.accuracy != right.calibrated_metrics.accuracy


def test_final_holdout_target_mutation_cannot_rewrite_calibrator_or_threshold() -> None:
    source = _source().final
    left = evaluate_final_source(_dataset(), source, _config())
    right = evaluate_final_source(
        _dataset(flip_final_holdout=True), source, _config()
    )

    assert _selection_hash(left) == _selection_hash(right)
    assert left.calibration_selection.fit == right.calibration_selection.fit
    assert left.edge_policy_selection.policy == right.edge_policy_selection.policy
    assert left.edge_policy_selection.min_edge == right.edge_policy_selection.min_edge
    assert left.calibrated_metrics is not None
    assert right.calibrated_metrics is not None
    assert left.calibrated_metrics.accuracy != right.calibrated_metrics.accuracy
    assert left.evaluation_partition == right.evaluation_partition == "holdout"


def test_service_reuses_phase8_offsets_and_keeps_holdout_outside_ordinary_oos(
    monkeypatch,
) -> None:
    import bp_engine.calibration.service as service

    source = _source()
    dataset = _dataset()
    monkeypatch.setattr(
        service,
        "load_backtest_source_spec",
        lambda connection, run_id: source,
    )
    monkeypatch.setattr(service, "load_dataset", lambda connection, **kwargs: dataset)

    report = run_calibration_edge_analysis(
        object(),
        source_backtest_run_id=source.run_id,
        start=START,
        end=END,
        edge_config=_config(),
        created_at=datetime(2026, 8, 26, tzinfo=UTC),
    )

    assert [fold.selected_offset_seconds for fold in report.folds] == [
        fold.selected_offset_seconds for fold in source.folds
    ]
    assert report.final_holdout.selected_offset_seconds == source.final.selected_offset_seconds
    ordinary = tuple(report.aggregate_oos.condition_ids)
    assert len(ordinary) == len(set(ordinary))
    assert set(ordinary).isdisjoint(report.final_holdout.holdout_condition_ids)
    assert all(fold.edge_selection_partition == "validation" for fold in report.folds)
    assert report.final_holdout.edge_selection_partition == "validation"
    assert report.config["fee_rate"] == 0.07
    assert report.config["slippage_buffer"] == 0.01


def test_registry_second_semantic_run_has_zero_row_delta(monkeypatch) -> None:
    import bp_engine.calibration.service as service

    source = _source()
    dataset = _dataset()
    monkeypatch.setattr(
        service,
        "load_backtest_source_spec",
        lambda connection, run_id: source,
    )
    monkeypatch.setattr(service, "load_dataset", lambda connection, **kwargs: dataset)

    first = run_calibration_edge_analysis(
        object(),
        source_backtest_run_id=source.run_id,
        start=START,
        end=END,
        edge_config=_config(),
        created_at=datetime(2026, 8, 26, tzinfo=UTC),
    )
    second = run_calibration_edge_analysis(
        object(),
        source_backtest_run_id=source.run_id,
        start=START,
        end=END,
        edge_config=_config(),
        created_at=datetime(2026, 8, 26, 1, tzinfo=UTC),
    )
    assert first.run_id == second.run_id
    assert first.semantic_sha256 == second.semantic_sha256

    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    repository = CalibrationEdgeRunRepository()
    with engine.begin() as connection:
        before = connection.execute(
            select(func.count()).select_from(calibration_edge_runs)
        ).scalar_one()
        first_store = repository.store(connection, first)
        after_first = connection.execute(
            select(func.count()).select_from(calibration_edge_runs)
        ).scalar_one()
        second_store = repository.store(connection, second)
        after_second = connection.execute(
            select(func.count()).select_from(calibration_edge_runs)
        ).scalar_one()

    assert before == 0
    assert after_first == 1
    assert after_second - after_first == 0
    assert first_store.created is True and first_store.existing is False
    assert second_store.created is False and second_store.existing is True
