from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from sqlalchemy import Connection

from bp_engine.backtesting.execution import execution_diagnostic
from bp_engine.backtesting.folds import build_walk_forward_plan
from bp_engine.backtesting.models import (
    BACKTEST_VERSION,
    BacktestReport,
    FinalHoldoutReport,
    FoldEvaluationReport,
    WalkForwardConfig,
)
from bp_engine.backtesting.predictor import MarketPriceFoldPredictor, load_model_spec
from bp_engine.backtesting.regimes import regime_metrics, training_volatility_threshold
from bp_engine.backtesting.selection import rows_at_offset, select_validation_offset
from bp_engine.backtesting.uncertainty import wilson_accuracy_interval
from bp_engine.features.hashing import canonical_hash
from bp_engine.modeling.dataset import load_dataset
from bp_engine.modeling.metrics import evaluate_probabilities
from bp_engine.modeling.models import DatasetSnapshot, MetricSummary, SupervisedRow
from bp_engine.modeling.split import equal_market_weights


class BacktestIntegrityError(RuntimeError):
    """Raised when a walk-forward report would violate frozen integrity gates."""


def _require_aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


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


def _rows_for_conditions(
    rows: tuple[SupervisedRow, ...], condition_ids: tuple[str, ...]
) -> tuple[SupervisedRow, ...]:
    allowed = set(condition_ids)
    return tuple(row for row in rows if row.condition_id in allowed)


def _selected_partition_rows(
    rows: tuple[SupervisedRow, ...],
    condition_ids: tuple[str, ...],
    offset_seconds: int,
) -> tuple[tuple[SupervisedRow, ...], tuple[str, ...], float]:
    selected = rows_at_offset(rows, offset_seconds)
    by_condition = {row.condition_id: row for row in selected}
    predicted = tuple(
        by_condition[condition_id]
        for condition_id in condition_ids
        if condition_id in by_condition
    )
    missing = tuple(
        condition_id for condition_id in condition_ids if condition_id not in by_condition
    )
    coverage = len(predicted) / len(condition_ids) if condition_ids else 0.0
    return predicted, missing, coverage


def _metrics(
    rows: tuple[SupervisedRow, ...], probabilities: tuple[float, ...]
) -> MetricSummary:
    if not rows:
        raise BacktestIntegrityError("cannot evaluate an empty prediction partition")
    return evaluate_probabilities(rows, probabilities, equal_market_weights(rows))


def _accuracy_interval(
    rows: tuple[SupervisedRow, ...], probabilities: tuple[float, ...]
) -> tuple[float, float]:
    correct = sum(
        row.target == (1 if probability >= 0.5 else 0)
        for row, probability in zip(rows, probabilities, strict=True)
    )
    return wilson_accuracy_interval(correct, len(rows))


def _evaluate_selected_partition(
    *,
    train_rows: tuple[SupervisedRow, ...],
    evaluation_rows: tuple[SupervisedRow, ...],
    expected_condition_ids: tuple[str, ...],
    selected_offset_seconds: int,
    min_prediction_coverage: float,
) -> tuple[
    tuple[SupervisedRow, ...],
    tuple[float, ...],
    tuple[str, ...],
    float,
    MetricSummary,
    tuple[float, float],
    float | None,
    dict[str, Any],
    dict[str, Any],
]:
    rows, missing, coverage = _selected_partition_rows(
        evaluation_rows,
        expected_condition_ids,
        selected_offset_seconds,
    )
    if coverage < min_prediction_coverage:
        raise BacktestIntegrityError(
            "prediction coverage below required minimum: "
            f"coverage={coverage:.6f} minimum={min_prediction_coverage:.6f}"
        )

    predictor = MarketPriceFoldPredictor()
    predictor.fit(train_rows)
    probabilities = predictor.predict(rows)
    metrics = _metrics(rows, probabilities)
    interval = _accuracy_interval(rows, probabilities)
    volatility_threshold = training_volatility_threshold(
        train_rows, selected_offset_seconds
    )
    execution = execution_diagnostic(rows, probabilities)
    regimes = regime_metrics(
        rows,
        probabilities,
        volatility_threshold=volatility_threshold,
    )
    return (
        rows,
        probabilities,
        missing,
        coverage,
        metrics,
        interval,
        volatility_threshold,
        execution,
        regimes,
    )


def _final_membership_hash(plan: Any) -> str:
    return canonical_hash(
        {
            "train": plan.final_train.condition_ids,
            "validation": plan.final_validation.condition_ids,
            "holdout": plan.final_holdout.condition_ids,
            "purged": plan.final_purged_condition_ids,
            "embargo": plan.final_embargo_condition_ids,
        }
    )


def _validate_dataset_contract(dataset: DatasetSnapshot, source: Any) -> None:
    expected = (
        source.dataset_version,
        source.feature_version,
        source.label_version,
        source.horizon_seconds,
    )
    actual = (
        dataset.dataset_version,
        dataset.feature_version,
        dataset.label_version,
        dataset.horizon_seconds,
    )
    if actual != expected:
        raise BacktestIntegrityError("dataset does not match source training contract")
    if not dataset.rows:
        raise BacktestIntegrityError("backtest dataset must not be empty")


def run_walk_forward_backtest(
    connection: Connection,
    *,
    source_training_run_id: str,
    start: datetime,
    end: datetime,
    config: WalkForwardConfig,
    created_at: datetime,
) -> BacktestReport:
    _require_aware("start", start)
    _require_aware("end", end)
    _require_aware("created_at", created_at)
    if end <= start:
        raise ValueError("start must be before end")

    source = load_model_spec(connection, source_training_run_id)
    if source.run_id != source_training_run_id:
        raise BacktestIntegrityError("source training run id mismatch")
    dataset = load_dataset(
        connection,
        start=start,
        end=end,
        horizon_seconds=source.horizon_seconds,
        feature_version=source.feature_version,
        label_version=source.label_version,
    )
    _validate_dataset_contract(dataset, source)
    plan = build_walk_forward_plan(dataset, config)

    fold_reports: list[FoldEvaluationReport] = []
    aggregate_rows: list[SupervisedRow] = []
    aggregate_probabilities: list[float] = []
    seen_oos: set[str] = set()

    for fold in plan.folds:
        train_rows = _rows_for_conditions(dataset.rows, fold.train.condition_ids)
        validation_rows = _rows_for_conditions(
            dataset.rows, fold.validation.condition_ids
        )
        test_rows = _rows_for_conditions(dataset.rows, fold.test.condition_ids)
        selection = select_validation_offset(
            train_rows,
            validation_rows,
            min_market_price_coverage=config.min_market_price_coverage,
            min_validation_markets=config.min_validation_markets,
        )
        (
            selected_rows,
            probabilities,
            missing,
            coverage,
            metrics,
            interval,
            volatility_threshold,
            execution,
            regimes,
        ) = _evaluate_selected_partition(
            train_rows=train_rows,
            evaluation_rows=test_rows,
            expected_condition_ids=fold.test.condition_ids,
            selected_offset_seconds=selection.selected_offset_seconds,
            min_prediction_coverage=config.min_prediction_coverage,
        )

        for row in selected_rows:
            if row.condition_id in seen_oos:
                raise BacktestIntegrityError(
                    f"ordinary OOS market reused: {row.condition_id}"
                )
            seen_oos.add(row.condition_id)
            aggregate_rows.append(row)
        aggregate_probabilities.extend(probabilities)
        fold_reports.append(
            FoldEvaluationReport(
                index=fold.index,
                membership_sha256=fold.membership_sha256,
                train_condition_ids=fold.train.condition_ids,
                validation_condition_ids=fold.validation.condition_ids,
                test_condition_ids=tuple(row.condition_id for row in selected_rows),
                selected_offset_seconds=selection.selected_offset_seconds,
                validation_candidates=selection.candidates,
                expected_test_markets=len(fold.test.condition_ids),
                predicted_test_markets=len(selected_rows),
                missing_offset_condition_ids=missing,
                prediction_coverage=coverage,
                metrics=metrics,
                accuracy_wilson_95=interval,
                volatility_threshold=volatility_threshold,
                execution=execution,
                regimes=regimes,
            )
        )

    aggregate_rows_tuple = tuple(aggregate_rows)
    aggregate_probabilities_tuple = tuple(aggregate_probabilities)
    aggregate_metrics = _metrics(aggregate_rows_tuple, aggregate_probabilities_tuple)
    aggregate_interval = _accuracy_interval(
        aggregate_rows_tuple, aggregate_probabilities_tuple
    )
    aggregate_execution = execution_diagnostic(
        aggregate_rows_tuple, aggregate_probabilities_tuple
    )

    final_train_rows = _rows_for_conditions(
        dataset.rows, plan.final_train.condition_ids
    )
    final_validation_rows = _rows_for_conditions(
        dataset.rows, plan.final_validation.condition_ids
    )
    final_holdout_rows = _rows_for_conditions(
        dataset.rows, plan.final_holdout.condition_ids
    )
    final_selection = select_validation_offset(
        final_train_rows,
        final_validation_rows,
        min_market_price_coverage=config.min_market_price_coverage,
        min_validation_markets=config.min_validation_markets,
    )
    (
        selected_holdout_rows,
        final_probabilities,
        final_missing,
        final_coverage,
        final_metrics,
        final_interval,
        final_volatility_threshold,
        final_execution,
        final_regimes,
    ) = _evaluate_selected_partition(
        train_rows=final_train_rows,
        evaluation_rows=final_holdout_rows,
        expected_condition_ids=plan.final_holdout.condition_ids,
        selected_offset_seconds=final_selection.selected_offset_seconds,
        min_prediction_coverage=config.min_prediction_coverage,
    )
    if seen_oos.intersection(row.condition_id for row in selected_holdout_rows):
        raise BacktestIntegrityError("final holdout overlaps ordinary OOS markets")

    final_membership_sha256 = _final_membership_hash(plan)
    final_report = FinalHoldoutReport(
        membership_sha256=final_membership_sha256,
        train_condition_ids=plan.final_train.condition_ids,
        validation_condition_ids=plan.final_validation.condition_ids,
        holdout_condition_ids=tuple(
            row.condition_id for row in selected_holdout_rows
        ),
        selected_offset_seconds=final_selection.selected_offset_seconds,
        validation_candidates=final_selection.candidates,
        expected_holdout_markets=len(plan.final_holdout.condition_ids),
        predicted_holdout_markets=len(selected_holdout_rows),
        missing_offset_condition_ids=final_missing,
        prediction_coverage=final_coverage,
        metrics=final_metrics,
        accuracy_wilson_95=final_interval,
        volatility_threshold=final_volatility_threshold,
        execution=final_execution,
        regimes=final_regimes,
    )

    config_payload = _config_payload(config)
    config_sha256 = canonical_hash(config_payload)
    fold_membership_sha256 = tuple(
        [*(fold.membership_sha256 for fold in plan.folds), final_membership_sha256]
    )
    semantic_payload = {
        "backtest_version": BACKTEST_VERSION,
        "source_training_run_id": source.run_id,
        "source_training_semantic_sha256": source.semantic_sha256,
        "dataset_version": dataset.dataset_version,
        "feature_version": dataset.feature_version,
        "label_version": dataset.label_version,
        "horizon_seconds": dataset.horizon_seconds,
        "start": start,
        "end": end,
        "dataset_sha256": dataset.dataset_sha256,
        "config": config_payload,
        "config_sha256": config_sha256,
        "plan_sha256": plan.plan_sha256,
        "fold_membership_sha256": fold_membership_sha256,
        "folds": tuple(asdict(report) for report in fold_reports),
        "aggregate_oos_condition_ids": tuple(
            row.condition_id for row in aggregate_rows_tuple
        ),
        "aggregate_oos_metrics": asdict(aggregate_metrics),
        "aggregate_oos_accuracy_wilson_95": aggregate_interval,
        "aggregate_oos_execution": aggregate_execution,
        "final_holdout": asdict(final_report),
    }
    semantic_sha256 = canonical_hash(semantic_payload)
    run_id = f"phase8-{dataset.horizon_seconds}-{semantic_sha256[:32]}"
    return BacktestReport(
        run_id=run_id,
        backtest_version=BACKTEST_VERSION,
        source_training_run_id=source.run_id,
        source_training_semantic_sha256=source.semantic_sha256,
        dataset_version=dataset.dataset_version,
        feature_version=dataset.feature_version,
        label_version=dataset.label_version,
        horizon_seconds=dataset.horizon_seconds,
        start=start,
        end=end,
        dataset_sha256=dataset.dataset_sha256,
        config=config_payload,
        config_sha256=config_sha256,
        plan_sha256=plan.plan_sha256,
        fold_membership_sha256=fold_membership_sha256,
        folds=tuple(fold_reports),
        aggregate_oos_condition_ids=tuple(
            row.condition_id for row in aggregate_rows_tuple
        ),
        aggregate_oos_metrics=aggregate_metrics,
        aggregate_oos_accuracy_wilson_95=aggregate_interval,
        aggregate_oos_execution=aggregate_execution,
        final_holdout=final_report,
        semantic_sha256=semantic_sha256,
        created_at=created_at,
    )
