from __future__ import annotations

import math
from dataclasses import dataclass

from bp_engine.backtesting.predictor import MarketPriceFoldPredictor
from bp_engine.backtesting.selection import rows_at_offset
from bp_engine.calibration.calibrators import apply_calibration, select_calibrator
from bp_engine.calibration.edge import (
    edge_decision,
    evaluate_edge_policy,
    select_validation_edge_policy,
)
from bp_engine.calibration.models import (
    CalibrationEdgeFinalReport,
    CalibrationEdgeFoldReport,
    CalibrationEdgePrediction,
    CalibrationSelection,
    EdgeConfig,
    EdgePolicyMetrics,
    EdgePolicySelection,
)
from bp_engine.calibration.source import FinalSourceSpec, SourceFoldSpec
from bp_engine.modeling.metrics import evaluate_probabilities
from bp_engine.modeling.models import DatasetSnapshot, MetricSummary, SupervisedRow
from bp_engine.modeling.split import equal_market_weights


@dataclass(frozen=True)
class _EvaluationCore:
    selected_rows: tuple[SupervisedRow, ...]
    missing_condition_ids: tuple[str, ...]
    prediction_coverage: float
    observed_market_price_count: int
    observed_market_price_coverage: float
    calibration_selection: CalibrationSelection
    edge_policy_selection: EdgePolicySelection
    raw_metrics: MetricSummary | None
    calibrated_metrics: MetricSummary | None
    edge_metrics: EdgePolicyMetrics
    predictions: tuple[CalibrationEdgePrediction, ...]


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
    ordered = tuple(
        by_condition[condition_id]
        for condition_id in condition_ids
        if condition_id in by_condition
    )
    missing = tuple(
        condition_id for condition_id in condition_ids if condition_id not in by_condition
    )
    coverage = len(ordered) / len(condition_ids) if condition_ids else 0.0
    return ordered, missing, coverage


def _market_probability_observed(row: SupervisedRow) -> bool:
    value = row.predictors.get("pm_up_price")
    if value is None:
        return False
    numeric = float(value)
    return math.isfinite(numeric) and 0.0 <= numeric <= 1.0


def _observed_inputs(
    rows: tuple[SupervisedRow, ...], probabilities: tuple[float, ...]
) -> tuple[tuple[SupervisedRow, ...], tuple[float, ...]]:
    if len(rows) != len(probabilities):
        raise ValueError("rows and probabilities must have equal length")
    selected_rows: list[SupervisedRow] = []
    selected_probabilities: list[float] = []
    for row, probability in zip(rows, probabilities, strict=True):
        if _market_probability_observed(row):
            selected_rows.append(row)
            selected_probabilities.append(probability)
    return tuple(selected_rows), tuple(selected_probabilities)


def _metric_pair(
    rows: tuple[SupervisedRow, ...],
    raw_probabilities: tuple[float, ...],
    calibrated_probabilities: tuple[float, ...],
) -> tuple[MetricSummary | None, MetricSummary | None]:
    observed_rows, observed_raw = _observed_inputs(rows, raw_probabilities)
    if not observed_rows:
        return None, None
    observed_calibrated = tuple(
        calibrated
        for row, calibrated in zip(rows, calibrated_probabilities, strict=True)
        if _market_probability_observed(row)
    )
    weights = equal_market_weights(observed_rows)
    return (
        evaluate_probabilities(observed_rows, observed_raw, weights),
        evaluate_probabilities(observed_rows, observed_calibrated, weights),
    )


def _prediction_reports(
    rows: tuple[SupervisedRow, ...],
    raw_probabilities: tuple[float, ...],
    calibrated_probabilities: tuple[float, ...],
    edge_config: EdgeConfig,
    min_edge: float | None,
) -> tuple[CalibrationEdgePrediction, ...]:
    reports: list[CalibrationEdgePrediction] = []
    for row, raw, calibrated in zip(
        rows, raw_probabilities, calibrated_probabilities, strict=True
    ):
        reports.append(
            CalibrationEdgePrediction(
                condition_id=row.condition_id,
                target=row.target,
                raw_probability=raw,
                calibrated_probability=calibrated,
                market_probability_observed=_market_probability_observed(row),
                edge_decision=edge_decision(row, calibrated, edge_config, min_edge),
            )
        )
    return tuple(reports)


def _fit_and_select(
    dataset: DatasetSnapshot,
    train_condition_ids: tuple[str, ...],
    validation_condition_ids: tuple[str, ...],
    offset_seconds: int,
    edge_config: EdgeConfig,
) -> tuple[
    MarketPriceFoldPredictor,
    CalibrationSelection,
    EdgePolicySelection,
]:
    train_all_rows = _rows_for_conditions(dataset.rows, train_condition_ids)
    validation_all_rows = _rows_for_conditions(dataset.rows, validation_condition_ids)
    train_rows, train_missing, _ = _selected_partition_rows(
        train_all_rows, train_condition_ids, offset_seconds
    )
    validation_rows, validation_missing, _ = _selected_partition_rows(
        validation_all_rows, validation_condition_ids, offset_seconds
    )
    if train_missing:
        raise ValueError(
            "calibration train partition is missing the frozen offset for "
            f"{train_missing[0]}"
        )
    if validation_missing:
        raise ValueError(
            "calibration validation partition is missing the frozen offset for "
            f"{validation_missing[0]}"
        )

    predictor = MarketPriceFoldPredictor()
    predictor.fit(train_all_rows)
    raw_train = predictor.predict(train_rows)
    raw_validation = predictor.predict(validation_rows)
    observed_train_rows, observed_train = _observed_inputs(train_rows, raw_train)
    observed_validation_rows, observed_validation = _observed_inputs(
        validation_rows, raw_validation
    )
    if not observed_train_rows:
        raise ValueError("calibration train partition has no observed market probabilities")
    if not observed_validation_rows:
        raise ValueError(
            "calibration validation partition has no observed market probabilities"
        )

    calibration_selection = select_calibrator(
        observed_train_rows,
        observed_train,
        observed_validation_rows,
        observed_validation,
    )
    calibrated_validation = apply_calibration(
        calibration_selection.fit, raw_validation
    )
    edge_policy_selection = select_validation_edge_policy(
        validation_rows,
        calibrated_validation,
        edge_config,
    )
    return predictor, calibration_selection, edge_policy_selection


def _evaluate_partition(
    dataset: DatasetSnapshot,
    *,
    train_condition_ids: tuple[str, ...],
    validation_condition_ids: tuple[str, ...],
    evaluation_condition_ids: tuple[str, ...],
    offset_seconds: int,
    edge_config: EdgeConfig,
) -> _EvaluationCore:
    predictor, calibration_selection, edge_policy_selection = _fit_and_select(
        dataset,
        train_condition_ids,
        validation_condition_ids,
        offset_seconds,
        edge_config,
    )

    evaluation_all_rows = _rows_for_conditions(dataset.rows, evaluation_condition_ids)
    evaluation_rows, missing, coverage = _selected_partition_rows(
        evaluation_all_rows,
        evaluation_condition_ids,
        offset_seconds,
    )
    if not evaluation_rows:
        raise ValueError("evaluation partition has no rows at the frozen offset")

    raw_probabilities = predictor.predict(evaluation_rows)
    calibrated_probabilities = apply_calibration(
        calibration_selection.fit, raw_probabilities
    )
    raw_metrics, calibrated_metrics = _metric_pair(
        evaluation_rows,
        raw_probabilities,
        calibrated_probabilities,
    )
    edge_metrics = evaluate_edge_policy(
        evaluation_rows,
        calibrated_probabilities,
        edge_config,
        edge_policy_selection.min_edge,
    )
    predictions = _prediction_reports(
        evaluation_rows,
        raw_probabilities,
        calibrated_probabilities,
        edge_config,
        edge_policy_selection.min_edge,
    )
    observed = sum(_market_probability_observed(row) for row in evaluation_rows)
    observed_coverage = observed / len(evaluation_rows)
    return _EvaluationCore(
        selected_rows=evaluation_rows,
        missing_condition_ids=missing,
        prediction_coverage=coverage,
        observed_market_price_count=observed,
        observed_market_price_coverage=observed_coverage,
        calibration_selection=calibration_selection,
        edge_policy_selection=edge_policy_selection,
        raw_metrics=raw_metrics,
        calibrated_metrics=calibrated_metrics,
        edge_metrics=edge_metrics,
        predictions=predictions,
    )


def evaluate_source_fold(
    dataset: DatasetSnapshot,
    source: SourceFoldSpec,
    edge_config: EdgeConfig,
) -> CalibrationEdgeFoldReport:
    core = _evaluate_partition(
        dataset,
        train_condition_ids=source.train_condition_ids,
        validation_condition_ids=source.validation_condition_ids,
        evaluation_condition_ids=source.test_condition_ids,
        offset_seconds=source.selected_offset_seconds,
        edge_config=edge_config,
    )
    return CalibrationEdgeFoldReport(
        index=source.index,
        membership_sha256=source.membership_sha256,
        train_condition_ids=source.train_condition_ids,
        validation_condition_ids=source.validation_condition_ids,
        test_condition_ids=tuple(row.condition_id for row in core.selected_rows),
        selected_offset_seconds=source.selected_offset_seconds,
        calibration_selection_fit_partition="train",
        calibration_selection_partition="validation",
        edge_selection_partition="validation",
        evaluation_partition="test",
        expected_test_markets=len(source.test_condition_ids),
        predicted_test_markets=len(core.selected_rows),
        missing_offset_condition_ids=core.missing_condition_ids,
        prediction_coverage=core.prediction_coverage,
        observed_market_price_test_markets=core.observed_market_price_count,
        observed_market_price_coverage=core.observed_market_price_coverage,
        calibration_selection=core.calibration_selection,
        edge_policy_selection=core.edge_policy_selection,
        raw_metrics=core.raw_metrics,
        calibrated_metrics=core.calibrated_metrics,
        edge_metrics=core.edge_metrics,
        predictions=core.predictions,
    )


def evaluate_final_source(
    dataset: DatasetSnapshot,
    source: FinalSourceSpec,
    edge_config: EdgeConfig,
) -> CalibrationEdgeFinalReport:
    core = _evaluate_partition(
        dataset,
        train_condition_ids=source.train_condition_ids,
        validation_condition_ids=source.validation_condition_ids,
        evaluation_condition_ids=source.holdout_condition_ids,
        offset_seconds=source.selected_offset_seconds,
        edge_config=edge_config,
    )
    return CalibrationEdgeFinalReport(
        membership_sha256=source.membership_sha256,
        train_condition_ids=source.train_condition_ids,
        validation_condition_ids=source.validation_condition_ids,
        holdout_condition_ids=tuple(row.condition_id for row in core.selected_rows),
        selected_offset_seconds=source.selected_offset_seconds,
        calibration_selection_fit_partition="train",
        calibration_selection_partition="validation",
        edge_selection_partition="validation",
        evaluation_partition="holdout",
        expected_holdout_markets=len(source.holdout_condition_ids),
        predicted_holdout_markets=len(core.selected_rows),
        missing_offset_condition_ids=core.missing_condition_ids,
        prediction_coverage=core.prediction_coverage,
        observed_market_price_holdout_markets=core.observed_market_price_count,
        observed_market_price_coverage=core.observed_market_price_coverage,
        calibration_selection=core.calibration_selection,
        edge_policy_selection=core.edge_policy_selection,
        raw_metrics=core.raw_metrics,
        calibrated_metrics=core.calibrated_metrics,
        edge_metrics=core.edge_metrics,
        predictions=core.predictions,
    )
