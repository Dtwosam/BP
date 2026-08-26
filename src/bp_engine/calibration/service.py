from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import datetime
from typing import Any

from sqlalchemy import Connection

from bp_engine.calibration.evaluation import evaluate_final_source, evaluate_source_fold
from bp_engine.calibration.models import (
    CALIBRATION_VERSION,
    EDGE_POLICY_VERSION,
    CalibrationEdgeAggregateReport,
    CalibrationEdgeFoldReport,
    CalibrationEdgePrediction,
    CalibrationEdgeReport,
    EdgeConfig,
    EdgePolicyMetrics,
)
from bp_engine.calibration.source import BacktestSourceSpec, load_backtest_source_spec
from bp_engine.features.hashing import canonical_hash
from bp_engine.modeling.dataset import load_dataset
from bp_engine.modeling.metrics import evaluate_probabilities
from bp_engine.modeling.models import DatasetSnapshot, MetricSummary, SupervisedRow
from bp_engine.modeling.split import equal_market_weights


class CalibrationEdgeIntegrityError(RuntimeError):
    """Raised when Phase 9 orchestration violates frozen provenance gates."""


def _require_aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _config_payload(config: EdgeConfig) -> dict[str, Any]:
    return {
        "fee_rate": config.fee_rate,
        "slippage_buffer": config.slippage_buffer,
        "min_edge_grid": list(config.min_edge_grid),
        "min_validation_trades": config.min_validation_trades,
        "max_spread": config.max_spread,
    }


def _validate_source_partitions(source: BacktestSourceSpec) -> None:
    seen_oos: set[str] = set()
    for fold in source.folds:
        for condition_id in fold.test_condition_ids:
            if condition_id in seen_oos:
                raise CalibrationEdgeIntegrityError(
                    f"ordinary OOS market reused: {condition_id}"
                )
            seen_oos.add(condition_id)
    overlap = seen_oos.intersection(source.final.holdout_condition_ids)
    if overlap:
        raise CalibrationEdgeIntegrityError(
            f"final holdout overlaps ordinary OOS market: {sorted(overlap)[0]}"
        )


def _validate_dataset(dataset: DatasetSnapshot, source: BacktestSourceSpec) -> None:
    checks = (
        ("dataset_version", dataset.dataset_version, source.dataset_version),
        ("feature_version", dataset.feature_version, source.feature_version),
        ("label_version", dataset.label_version, source.label_version),
        ("horizon_seconds", dataset.horizon_seconds, source.horizon_seconds),
        ("start", dataset.start, source.start),
        ("end", dataset.end, source.end),
        ("dataset_sha256", dataset.dataset_sha256, source.dataset_sha256),
    )
    for name, actual, expected in checks:
        if actual != expected:
            raise CalibrationEdgeIntegrityError(
                f"reconstructed {name} does not match source Phase 8 run"
            )
    if not dataset.rows:
        raise CalibrationEdgeIntegrityError("reconstructed dataset must not be empty")


def _probability_metrics(
    dataset: DatasetSnapshot,
    folds: tuple[CalibrationEdgeFoldReport, ...],
) -> tuple[MetricSummary | None, MetricSummary | None]:
    lookup = {
        (row.condition_id, row.feature_offset_seconds): row for row in dataset.rows
    }
    rows: list[SupervisedRow] = []
    raw: list[float] = []
    calibrated: list[float] = []
    for fold in folds:
        for prediction in fold.predictions:
            if not prediction.market_probability_observed:
                continue
            key = (prediction.condition_id, fold.selected_offset_seconds)
            row = lookup.get(key)
            if row is None:
                raise CalibrationEdgeIntegrityError(
                    f"aggregate OOS row missing from dataset: {prediction.condition_id}"
                )
            if row.target != prediction.target:
                raise CalibrationEdgeIntegrityError(
                    f"aggregate OOS target mismatch: {prediction.condition_id}"
                )
            rows.append(row)
            raw.append(prediction.raw_probability)
            calibrated.append(prediction.calibrated_probability)
    if not rows:
        return None, None
    rows_tuple = tuple(rows)
    weights = equal_market_weights(rows_tuple)
    return (
        evaluate_probabilities(rows_tuple, tuple(raw), weights),
        evaluate_probabilities(rows_tuple, tuple(calibrated), weights),
    )


def _aggregate_edge_metrics(
    predictions: tuple[CalibrationEdgePrediction, ...],
) -> EdgePolicyMetrics:
    reasons: Counter[str] = Counter()
    asks: list[float] = []
    spreads: list[float] = []
    raw_edges: list[float] = []
    adjusted_edges: list[float] = []
    fees: list[float] = []
    slippage: list[float] = []
    gross_pnl: list[float] = []
    after_cost_pnl: list[float] = []
    observed = 0
    executable = 0
    correct = 0

    for prediction in predictions:
        decision = prediction.edge_decision
        reasons[decision.reason] += 1
        observed += int(decision.market_probability_observed)
        if not decision.executable:
            continue
        executable += 1
        if decision.ask is None:
            raise CalibrationEdgeIntegrityError("executable decision is missing ask")
        asks.append(decision.ask)
        if decision.spread is not None:
            spreads.append(decision.spread)
        if not decision.trade:
            continue
        if decision.raw_edge is None or decision.cost_adjusted_edge is None:
            raise CalibrationEdgeIntegrityError("trade decision is missing edge values")
        raw_edges.append(decision.raw_edge)
        adjusted_edges.append(decision.cost_adjusted_edge)
        fees.append(decision.fee)
        slippage.append(decision.slippage_buffer)
        is_correct = prediction.target == decision.predicted_target
        correct += int(is_correct)
        payout = 1.0 if is_correct else 0.0
        gross = payout - decision.ask
        gross_pnl.append(gross)
        after_cost_pnl.append(gross - decision.fee - decision.slippage_buffer)

    prediction_markets = len(predictions)
    trade_count = len(gross_pnl)
    no_fill = prediction_markets - executable
    abstained_edge = executable - trade_count
    return EdgePolicyMetrics(
        prediction_markets=prediction_markets,
        market_probability_observed_markets=observed,
        executable_markets=executable,
        trade_count=trade_count,
        no_fill_markets=no_fill,
        abstained_edge_markets=abstained_edge,
        reason_counts=dict(sorted(reasons.items())),
        trade_coverage=(trade_count / prediction_markets if prediction_markets else 0.0),
        average_observed_ask=sum(asks) / len(asks) if asks else None,
        average_observed_spread=sum(spreads) / len(spreads) if spreads else None,
        correct_trades=correct,
        traded_accuracy=correct / trade_count if trade_count else None,
        raw_expected_edge_sum=sum(raw_edges),
        mean_raw_expected_edge=sum(raw_edges) / trade_count if trade_count else None,
        fee_sum=sum(fees),
        slippage_sum=sum(slippage),
        cost_adjusted_expected_edge_sum=sum(adjusted_edges),
        mean_cost_adjusted_expected_edge=(
            sum(adjusted_edges) / trade_count if trade_count else None
        ),
        gross_realized_pnl_before_costs=sum(gross_pnl),
        realized_pnl_after_assumed_costs=sum(after_cost_pnl),
        mean_realized_pnl_after_assumed_costs=(
            sum(after_cost_pnl) / trade_count if trade_count else None
        ),
    )


def _aggregate_oos(
    dataset: DatasetSnapshot,
    folds: tuple[CalibrationEdgeFoldReport, ...],
) -> CalibrationEdgeAggregateReport:
    predictions = tuple(
        prediction for fold in folds for prediction in fold.predictions
    )
    condition_ids = tuple(prediction.condition_id for prediction in predictions)
    if len(condition_ids) != len(set(condition_ids)):
        raise CalibrationEdgeIntegrityError("ordinary OOS predictions contain duplicates")
    raw_metrics, calibrated_metrics = _probability_metrics(dataset, folds)
    return CalibrationEdgeAggregateReport(
        condition_ids=condition_ids,
        raw_metrics=raw_metrics,
        calibrated_metrics=calibrated_metrics,
        edge_metrics=_aggregate_edge_metrics(predictions),
        predictions=predictions,
    )


def run_calibration_edge_analysis(
    connection: Connection,
    *,
    source_backtest_run_id: str,
    start: datetime,
    end: datetime,
    edge_config: EdgeConfig,
    created_at: datetime,
) -> CalibrationEdgeReport:
    _require_aware("start", start)
    _require_aware("end", end)
    _require_aware("created_at", created_at)
    if end <= start:
        raise ValueError("start must be before end")

    source = load_backtest_source_spec(connection, source_backtest_run_id)
    if source.run_id != source_backtest_run_id:
        raise CalibrationEdgeIntegrityError("source backtest run id mismatch")
    if start != source.start or end != source.end:
        raise CalibrationEdgeIntegrityError(
            "requested window must exactly match source window"
        )
    _validate_source_partitions(source)

    dataset = load_dataset(
        connection,
        start=start,
        end=end,
        horizon_seconds=source.horizon_seconds,
        feature_version=source.feature_version,
        label_version=source.label_version,
    )
    _validate_dataset(dataset, source)

    fold_reports = tuple(
        evaluate_source_fold(dataset, fold, edge_config) for fold in source.folds
    )
    aggregate_oos = _aggregate_oos(dataset, fold_reports)
    final_holdout = evaluate_final_source(dataset, source.final, edge_config)

    config = _config_payload(edge_config)
    config_sha256 = canonical_hash(config)
    semantic_payload = {
        "calibration_version": CALIBRATION_VERSION,
        "edge_policy_version": EDGE_POLICY_VERSION,
        "source_backtest_run_id": source.run_id,
        "source_backtest_version": source.backtest_version,
        "source_backtest_semantic_sha256": source.semantic_sha256,
        "source_training_run_id": source.source_training_run_id,
        "source_training_semantic_sha256": source.source_training_semantic_sha256,
        "dataset_version": dataset.dataset_version,
        "feature_version": dataset.feature_version,
        "label_version": dataset.label_version,
        "horizon_seconds": dataset.horizon_seconds,
        "start": start,
        "end": end,
        "dataset_sha256": dataset.dataset_sha256,
        "config": config,
        "config_sha256": config_sha256,
        "source_backtest_config_sha256": source.config_sha256,
        "source_plan_sha256": source.plan_sha256,
        "source_fold_membership_sha256": source.fold_membership_sha256,
        "folds": tuple(asdict(report) for report in fold_reports),
        "aggregate_oos": asdict(aggregate_oos),
        "final_holdout": asdict(final_holdout),
    }
    semantic_sha256 = canonical_hash(semantic_payload)
    run_id = f"phase9-{dataset.horizon_seconds}-{semantic_sha256[:32]}"
    return CalibrationEdgeReport(
        run_id=run_id,
        calibration_version=CALIBRATION_VERSION,
        edge_policy_version=EDGE_POLICY_VERSION,
        source_backtest_run_id=source.run_id,
        source_backtest_version=source.backtest_version,
        source_backtest_semantic_sha256=source.semantic_sha256,
        source_training_run_id=source.source_training_run_id,
        source_training_semantic_sha256=source.source_training_semantic_sha256,
        dataset_version=dataset.dataset_version,
        feature_version=dataset.feature_version,
        label_version=dataset.label_version,
        horizon_seconds=dataset.horizon_seconds,
        start=start,
        end=end,
        dataset_sha256=dataset.dataset_sha256,
        config=config,
        config_sha256=config_sha256,
        source_backtest_config_sha256=source.config_sha256,
        source_plan_sha256=source.plan_sha256,
        source_fold_membership_sha256=source.fold_membership_sha256,
        folds=fold_reports,
        aggregate_oos=aggregate_oos,
        final_holdout=final_holdout,
        semantic_sha256=semantic_sha256,
        created_at=created_at,
    )
