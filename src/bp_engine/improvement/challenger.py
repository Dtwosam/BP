from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.engine import Connection

from bp_engine.calibration.calibrators import apply_calibration
from bp_engine.calibration.edge import select_validation_edge_policy
from bp_engine.calibration.evaluation import (
    _fit_and_select,
    _prediction_reports,
    _rows_for_conditions,
    _selected_partition_rows,
)
from bp_engine.calibration.models import (
    CalibrationEdgePrediction,
    EdgeConfig,
    EdgePolicySelection,
)
from bp_engine.calibration.service import _aggregate_edge_metrics
from bp_engine.calibration.source import load_backtest_source_spec
from bp_engine.improvement.comparison import compare_policies
from bp_engine.improvement.hashing import derive_id, derive_seed, semantic_sha256
from bp_engine.improvement.models import (
    EVALUATION_VERSION,
    EXPERIMENT_VERSION,
    ChampionRef,
    ChangeFamily,
    EvidenceItem,
    EvidenceRole,
    ImprovementEvaluationReport,
    ImprovementExperimentSpec,
    PolicyMetrics,
)
from bp_engine.improvement.service import _load_experiment
from bp_engine.improvement.source import load_champion_ref, load_phase9_report
from bp_engine.improvement.statistics import (
    max_drawdown,
    max_losing_streak,
    paired_bootstrap_mean_delta,
)
from bp_engine.modeling.dataset import load_dataset
from bp_engine.modeling.metrics import evaluate_probabilities
from bp_engine.modeling.models import DatasetSnapshot, MetricSummary, SupervisedRow
from bp_engine.modeling.split import equal_market_weights

ACCEPTED_PHASE9_5M_RUN_ID = "phase9-300-c9f0e00eb7836af08008c66909f8f179"
ACCEPTED_PHASE9_5M_SEMANTIC_SHA256 = (
    "c9f0e00eb7836af08008c66909f8f179f03089413426508469353c75bcbcae24"
)
MAX_SPREAD_GRID: tuple[float | None, ...] = (0.02, 0.04, 0.06, 0.08, 0.10, None)
TIE_BREAK_RULES = (
    "highest_validation_realized_pnl_after_assumed_costs",
    "highest_validation_cost_adjusted_expected_edge_sum",
    "lower_validation_trade_count",
    "tighter_max_spread_none_last",
)


class ChallengerIntegrityError(ValueError):
    """Raised when the spread challenger crosses a frozen Phase 13 boundary."""


@dataclass(frozen=True)
class SpreadGuardAnalysis:
    challenger_semantic_sha256: str
    challenger_config: dict[str, Any]
    evidence_manifest: tuple[EvidenceItem, ...]
    champion_metrics: PolicyMetrics
    challenger_metrics: PolicyMetrics
    paired_net_pnl: tuple[tuple[str, float, float], ...]


@dataclass(frozen=True)
class _FoldAnalysis:
    index: int
    selected_max_spread: float | None
    selected_policy: EdgePolicySelection
    selected_rows: tuple[SupervisedRow, ...]
    predictions: tuple[CalibrationEdgePrediction, ...]
    validation_candidates: dict[float | None, EdgePolicySelection]


def _aware_datetime(value: object, *, name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ChallengerIntegrityError(f"{name} must be a datetime")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ChallengerIntegrityError(f"{name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _mapping(value: object, *, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ChallengerIntegrityError(f"{name} must be a mapping")
    return value


def _sequence(value: object, *, name: str) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ChallengerIntegrityError(f"{name} must be a sequence")
    return tuple(value)


def _require_accepted_champion(champion: ChampionRef) -> None:
    if (
        champion.calibration_run_id != ACCEPTED_PHASE9_5M_RUN_ID
        or champion.calibration_semantic_sha256 != ACCEPTED_PHASE9_5M_SEMANTIC_SHA256
    ):
        raise ChallengerIntegrityError(
            "spread challenger must use the accepted Phase 9 champion"
        )


def _phase9_config(report: Mapping[str, Any]) -> EdgeConfig:
    config = _mapping(report.get("config"), name="Phase 9 config")
    return EdgeConfig(
        fee_rate=float(config["fee_rate"]),
        slippage_buffer=float(config["slippage_buffer"]),
        min_edge_grid=tuple(float(value) for value in config["min_edge_grid"]),
        min_validation_trades=int(config["min_validation_trades"]),
        max_spread=(
            None if config.get("max_spread") is None else float(config["max_spread"])
        ),
    )


def _definition_payload(
    *,
    champion: ChampionRef,
    report: Mapping[str, Any],
) -> dict[str, Any]:
    config = _phase9_config(report)
    fold_hashes = list(
        _sequence(
            report.get("source_fold_membership_sha256"),
            name="Phase 9 source fold membership",
        )
    )
    return {
        "kind": "spread_guard_v1",
        "max_spread_grid": list(MAX_SPREAD_GRID),
        "tie_break_rules": list(TIE_BREAK_RULES),
        "accepted_champion_semantic_sha256": champion.calibration_semantic_sha256,
        "source_fold_membership_sha256": fold_hashes,
        "cost_assumptions": {
            "fee_rate": config.fee_rate,
            "slippage_buffer": config.slippage_buffer,
        },
    }


def build_spread_guard_experiment(
    connection: Connection,
    *,
    created_at: datetime,
) -> ImprovementExperimentSpec:
    champion = load_champion_ref(connection, ACCEPTED_PHASE9_5M_RUN_ID)
    _require_accepted_champion(champion)
    report = load_phase9_report(connection, ACCEPTED_PHASE9_5M_RUN_ID)
    if report.get("run_id") != champion.calibration_run_id:
        raise ChallengerIntegrityError("Phase 9 report does not match accepted champion id")
    if report.get("semantic_sha256") != champion.calibration_semantic_sha256:
        raise ChallengerIntegrityError("Phase 9 report does not match accepted champion hash")
    if int(report.get("horizon_seconds", 0)) != 300:
        raise ChallengerIntegrityError("accepted Phase 9 champion must be 5m")

    definition = _definition_payload(champion=champion, report=report)
    definition_sha256 = semantic_sha256(definition)
    final_holdout = _mapping(report.get("final_holdout"), name="Phase 9 final holdout")
    legacy_ids = tuple(
        str(value)
        for value in _sequence(
            final_holdout.get("holdout_condition_ids"),
            name="Phase 9 final holdout condition ids",
        )
    )
    if not legacy_ids or len(legacy_ids) != len(set(legacy_ids)):
        raise ChallengerIntegrityError(
            "Phase 9 final holdout identifiers must be non-empty and unique"
        )

    return ImprovementExperimentSpec.build(
        experiment_version=EXPERIMENT_VERSION,
        hypothesis=(
            "A validation-selected max-spread abstention guard improves executable "
            "5m economics without degrading calibration."
        ),
        horizon_seconds=300,
        change_family=ChangeFamily.ABSTENTION,
        champion=champion,
        challenger={**definition, "definition_semantic_sha256": definition_sha256},
        source_versions={
            "dataset": str(report["dataset_version"]),
            "feature": str(report["feature_version"]),
            "label": str(report["label_version"]),
        },
        research_start=_aware_datetime(report["start"], name="Phase 9 start"),
        research_end=_aware_datetime(report["end"], name="Phase 9 end"),
        selection_policy={"allowed_roles": [EvidenceRole.DEVELOPMENT_VALIDATION.value]},
        confirmation_policy={
            "allowed_roles": [
                EvidenceRole.FRESH_HOLDOUT.value,
                EvidenceRole.PROSPECTIVE_PAPER.value,
            ]
        },
        cost_assumptions=dict(definition["cost_assumptions"]),
        primary_metric="net_pnl_delta",
        guardrail_metrics=("calibrated_log_loss", "calibrated_brier"),
        legacy_confirmation_identifiers=legacy_ids,
        created_at=created_at,
    )


def select_validation_max_spread(
    candidates: Mapping[float | None, EdgePolicySelection],
) -> float | None:
    if not candidates:
        raise ValueError("validation spread candidates must not be empty")

    def key(max_spread: float | None) -> tuple[float, float, int, float]:
        metrics = candidates[max_spread].validation_metrics
        spread_preference = -math.inf if max_spread is None else -float(max_spread)
        return (
            metrics.realized_pnl_after_assumed_costs,
            metrics.cost_adjusted_expected_edge_sum,
            -metrics.trade_count,
            spread_preference,
        )

    return max(candidates, key=key)


def _validate_dataset(dataset: DatasetSnapshot, report: Mapping[str, Any]) -> None:
    expected = (
        ("dataset_version", dataset.dataset_version, report["dataset_version"]),
        ("feature_version", dataset.feature_version, report["feature_version"]),
        ("label_version", dataset.label_version, report["label_version"]),
        ("horizon_seconds", dataset.horizon_seconds, report["horizon_seconds"]),
        ("dataset_sha256", dataset.dataset_sha256, report["dataset_sha256"]),
    )
    for name, actual, frozen in expected:
        if actual != frozen:
            raise ChallengerIntegrityError(
                f"reconstructed {name} does not match accepted Phase 9 champion"
            )
    if not dataset.rows:
        raise ChallengerIntegrityError("reconstructed dataset must not be empty")


def _fold_analysis(
    dataset: DatasetSnapshot,
    *,
    fold: Any,
    base_config: EdgeConfig,
) -> _FoldAnalysis:
    predictor, calibration_selection, _ = _fit_and_select(
        dataset,
        fold.train_condition_ids,
        fold.validation_condition_ids,
        fold.selected_offset_seconds,
        base_config,
    )
    validation_all = _rows_for_conditions(dataset.rows, fold.validation_condition_ids)
    validation_rows, validation_missing, _ = _selected_partition_rows(
        validation_all,
        fold.validation_condition_ids,
        fold.selected_offset_seconds,
    )
    if validation_missing:
        raise ChallengerIntegrityError(
            f"validation partition missing frozen offset: {validation_missing[0]}"
        )
    raw_validation = predictor.predict(validation_rows)
    calibrated_validation = apply_calibration(
        calibration_selection.fit,
        raw_validation,
    )

    candidates: dict[float | None, EdgePolicySelection] = {}
    for max_spread in MAX_SPREAD_GRID:
        config = replace(base_config, max_spread=max_spread)
        candidates[max_spread] = select_validation_edge_policy(
            validation_rows,
            calibrated_validation,
            config,
        )
    selected_max_spread = select_validation_max_spread(candidates)
    selected_policy = candidates[selected_max_spread]
    selected_config = replace(base_config, max_spread=selected_max_spread)

    test_all = _rows_for_conditions(dataset.rows, fold.test_condition_ids)
    test_rows, test_missing, _ = _selected_partition_rows(
        test_all,
        fold.test_condition_ids,
        fold.selected_offset_seconds,
    )
    if test_missing:
        raise ChallengerIntegrityError(
            f"ordinary OOS partition missing frozen offset: {test_missing[0]}"
        )
    raw_test = predictor.predict(test_rows)
    calibrated_test = apply_calibration(calibration_selection.fit, raw_test)
    predictions = _prediction_reports(
        test_rows,
        raw_test,
        calibrated_test,
        selected_config,
        selected_policy.min_edge,
    )
    return _FoldAnalysis(
        index=int(fold.index),
        selected_max_spread=selected_max_spread,
        selected_policy=selected_policy,
        selected_rows=test_rows,
        predictions=predictions,
        validation_candidates=candidates,
    )


def _pooled_calibrated_metrics(
    folds: tuple[_FoldAnalysis, ...],
) -> MetricSummary:
    rows: list[SupervisedRow] = []
    probabilities: list[float] = []
    for fold in folds:
        row_by_condition = {row.condition_id: row for row in fold.selected_rows}
        for prediction in fold.predictions:
            if not prediction.market_probability_observed:
                continue
            row = row_by_condition.get(prediction.condition_id)
            if row is None:
                raise ChallengerIntegrityError(
                    f"challenger row missing for {prediction.condition_id}"
                )
            rows.append(row)
            probabilities.append(prediction.calibrated_probability)
    if not rows:
        raise ChallengerIntegrityError(
            "ordinary OOS challenger has no observed market probabilities"
        )
    rows_tuple = tuple(rows)
    return evaluate_probabilities(
        rows_tuple,
        tuple(probabilities),
        equal_market_weights(rows_tuple),
    )


def _object_prediction_net_pnl(prediction: CalibrationEdgePrediction) -> float:
    decision = prediction.edge_decision
    if not decision.trade:
        return 0.0
    if decision.ask is None:
        raise ChallengerIntegrityError("trade decision is missing ask")
    payout = 1.0 if prediction.target == decision.predicted_target else 0.0
    return payout - decision.ask - decision.fee - decision.slippage_buffer


def _stored_prediction_net_pnl(prediction: Mapping[str, Any]) -> float:
    decision = _mapping(prediction.get("edge_decision"), name="stored edge decision")
    if not bool(decision.get("trade")):
        return 0.0
    ask = decision.get("ask")
    if ask is None:
        raise ChallengerIntegrityError("stored trade decision is missing ask")
    payout = (
        1.0
        if int(prediction["target"]) == int(decision["predicted_target"])
        else 0.0
    )
    return (
        payout
        - float(ask)
        - float(decision.get("fee", 0.0))
        - float(decision.get("slippage_buffer", 0.0))
    )


def _phase9_policy_metrics(aggregate: Mapping[str, Any]) -> PolicyMetrics:
    calibrated = _mapping(
        aggregate.get("calibrated_metrics"),
        name="Phase 9 aggregate calibrated metrics",
    )
    edge = _mapping(aggregate.get("edge_metrics"), name="Phase 9 aggregate edge metrics")
    predictions = tuple(
        _mapping(item, name="Phase 9 aggregate prediction")
        for item in _sequence(
            aggregate.get("predictions"),
            name="Phase 9 aggregate predictions",
        )
    )
    trade_pnl = tuple(
        _stored_prediction_net_pnl(prediction)
        for prediction in predictions
        if bool(_mapping(prediction["edge_decision"], name="edge decision").get("trade"))
    )
    trade_count = int(edge["trade_count"])
    total_net_pnl = float(edge["realized_pnl_after_assumed_costs"])
    mean_net_pnl = edge.get("mean_realized_pnl_after_assumed_costs")
    return PolicyMetrics(
        calibrated_log_loss=float(calibrated["log_loss"]),
        calibrated_brier=float(calibrated["brier_score"]),
        ece=float(calibrated["ece"]),
        accuracy=float(calibrated["accuracy"]),
        trade_count=trade_count,
        trade_coverage=float(edge["trade_coverage"]),
        total_net_pnl=total_net_pnl,
        mean_net_pnl_per_trade=(
            float(mean_net_pnl) if mean_net_pnl is not None else 0.0
        ),
        max_drawdown=max_drawdown(trade_pnl),
        max_losing_streak=max_losing_streak(trade_pnl),
        unresolved_count=0,
        missing_unexecutable_count=int(edge["no_fill_markets"]),
    )


def _challenger_policy_metrics(
    calibrated: MetricSummary,
    predictions: tuple[CalibrationEdgePrediction, ...],
) -> PolicyMetrics:
    edge = _aggregate_edge_metrics(predictions)
    trade_pnl = tuple(
        _object_prediction_net_pnl(prediction)
        for prediction in predictions
        if prediction.edge_decision.trade
    )
    return PolicyMetrics(
        calibrated_log_loss=calibrated.log_loss,
        calibrated_brier=calibrated.brier_score,
        ece=calibrated.ece,
        accuracy=calibrated.accuracy,
        trade_count=edge.trade_count,
        trade_coverage=edge.trade_coverage,
        total_net_pnl=edge.realized_pnl_after_assumed_costs,
        mean_net_pnl_per_trade=(
            edge.mean_realized_pnl_after_assumed_costs
            if edge.mean_realized_pnl_after_assumed_costs is not None
            else 0.0
        ),
        max_drawdown=max_drawdown(trade_pnl),
        max_losing_streak=max_losing_streak(trade_pnl),
        unresolved_count=0,
        missing_unexecutable_count=edge.no_fill_markets,
    )


def _evidence_manifest(
    dataset: DatasetSnapshot,
    *,
    ordinary_net_pnl: Mapping[str, float],
    legacy_holdout_ids: tuple[str, ...],
) -> tuple[EvidenceItem, ...]:
    rows_by_condition: dict[str, SupervisedRow] = {}
    for row in dataset.rows:
        rows_by_condition.setdefault(row.condition_id, row)

    items: list[EvidenceItem] = []
    for condition_id in sorted(ordinary_net_pnl):
        row = rows_by_condition.get(condition_id)
        if row is None:
            raise ChallengerIntegrityError(
                f"ordinary OOS evidence row missing: {condition_id}"
            )
        items.append(
            EvidenceItem(
                identifier=condition_id,
                role=EvidenceRole.ORDINARY_OOS,
                condition_id=condition_id,
                prediction_id=None,
                observed_at=row.market_end_at,
                resolved=True,
                net_pnl=ordinary_net_pnl[condition_id],
            )
        )
    for condition_id in sorted(legacy_holdout_ids):
        row = rows_by_condition.get(condition_id)
        if row is None:
            raise ChallengerIntegrityError(
                f"legacy holdout evidence row missing: {condition_id}"
            )
        items.append(
            EvidenceItem(
                identifier=condition_id,
                role=EvidenceRole.ORDINARY_OOS,
                condition_id=condition_id,
                prediction_id=None,
                observed_at=row.market_end_at,
                resolved=True,
                net_pnl=None,
            )
        )
    return tuple(items)


def _analyze_legacy_source(
    connection: Connection,
    *,
    experiment: ImprovementExperimentSpec,
) -> SpreadGuardAnalysis:
    _require_accepted_champion(experiment.champion)
    phase9 = load_phase9_report(connection, experiment.champion.calibration_run_id)
    champion = load_champion_ref(connection, experiment.champion.calibration_run_id)
    if champion != experiment.champion:
        raise ChallengerIntegrityError(
            "registered experiment champion no longer matches immutable provenance"
        )
    source = load_backtest_source_spec(connection, champion.backtest_run_id)
    if source.semantic_sha256 != champion.backtest_semantic_sha256:
        raise ChallengerIntegrityError("Phase 8 source hash does not match registered champion")
    if source.source_training_semantic_sha256 != champion.training_semantic_sha256:
        raise ChallengerIntegrityError("Phase 7 source hash does not match registered champion")

    dataset = load_dataset(
        connection,
        start=source.start,
        end=source.end,
        horizon_seconds=source.horizon_seconds,
        feature_version=source.feature_version,
        label_version=source.label_version,
    )
    _validate_dataset(dataset, phase9)
    base_config = _phase9_config(phase9)

    folds = tuple(
        _fold_analysis(dataset, fold=fold, base_config=base_config)
        for fold in source.folds
    )
    challenger_predictions = tuple(
        prediction for fold in folds for prediction in fold.predictions
    )
    condition_ids = tuple(
        prediction.condition_id for prediction in challenger_predictions
    )
    if len(condition_ids) != len(set(condition_ids)):
        raise ChallengerIntegrityError("challenger ordinary OOS conditions contain duplicates")

    aggregate = _mapping(phase9.get("aggregate_oos"), name="Phase 9 aggregate OOS")
    champion_predictions = tuple(
        _mapping(item, name="Phase 9 aggregate prediction")
        for item in _sequence(
            aggregate.get("predictions"),
            name="Phase 9 aggregate predictions",
        )
    )
    champion_by_condition = {
        str(prediction["condition_id"]): _stored_prediction_net_pnl(prediction)
        for prediction in champion_predictions
    }
    challenger_by_condition = {
        prediction.condition_id: _object_prediction_net_pnl(prediction)
        for prediction in challenger_predictions
    }
    if set(champion_by_condition) != set(challenger_by_condition):
        raise ChallengerIntegrityError(
            "challenger ordinary OOS condition set differs from accepted Phase 9"
        )

    paired_net_pnl = tuple(
        (
            condition_id,
            champion_by_condition[condition_id],
            challenger_by_condition[condition_id],
        )
        for condition_id in sorted(challenger_by_condition)
    )
    calibrated = _pooled_calibrated_metrics(folds)
    champion_metrics = _phase9_policy_metrics(aggregate)
    challenger_metrics = _challenger_policy_metrics(
        calibrated,
        challenger_predictions,
    )

    selected_fold_guards = [
        {
            "fold_index": fold.index,
            "max_spread": fold.selected_max_spread,
            "min_edge": fold.selected_policy.min_edge,
            "validation_realized_pnl_after_assumed_costs": (
                fold.selected_policy.validation_metrics.realized_pnl_after_assumed_costs
            ),
            "validation_cost_adjusted_expected_edge_sum": (
                fold.selected_policy.validation_metrics.cost_adjusted_expected_edge_sum
            ),
            "validation_trade_count": fold.selected_policy.validation_metrics.trade_count,
        }
        for fold in folds
    ]
    semantic_payload = {
        "kind": "spread_guard_v1",
        "max_spread_grid": list(MAX_SPREAD_GRID),
        "tie_break_rules": list(TIE_BREAK_RULES),
        "accepted_champion_semantic_sha256": champion.calibration_semantic_sha256,
        "source_fold_membership_sha256": list(source.fold_membership_sha256),
        "cost_assumptions": {
            "fee_rate": base_config.fee_rate,
            "slippage_buffer": base_config.slippage_buffer,
        },
        "selected_fold_max_spreads": selected_fold_guards,
    }
    challenger_sha256 = semantic_sha256(semantic_payload)
    challenger_config = {
        **semantic_payload,
        "definition_semantic_sha256": experiment.challenger.get(
            "definition_semantic_sha256"
        ),
    }

    final_holdout = _mapping(phase9.get("final_holdout"), name="Phase 9 final holdout")
    legacy_ids = tuple(
        str(value)
        for value in _sequence(
            final_holdout.get("holdout_condition_ids"),
            name="Phase 9 final holdout condition ids",
        )
    )
    evidence = _evidence_manifest(
        dataset,
        ordinary_net_pnl=challenger_by_condition,
        legacy_holdout_ids=legacy_ids,
    )
    return SpreadGuardAnalysis(
        challenger_semantic_sha256=challenger_sha256,
        challenger_config=challenger_config,
        evidence_manifest=evidence,
        champion_metrics=champion_metrics,
        challenger_metrics=challenger_metrics,
        paired_net_pnl=paired_net_pnl,
    )


def evaluate_spread_guard_challenger(
    connection: Connection,
    *,
    experiment_id: str,
    created_at: datetime,
) -> ImprovementEvaluationReport:
    experiment = _load_experiment(connection, experiment_id)
    if experiment.horizon_seconds != 300:
        raise ChallengerIntegrityError("spread challenger is restricted to the 5m horizon")
    if experiment.challenger.get("kind") != "spread_guard_v1":
        raise ChallengerIntegrityError("experiment is not a spread_guard_v1 challenger")
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise ValueError("created_at must be timezone-aware")
    if created_at < experiment.created_at:
        raise ChallengerIntegrityError("evaluation cannot pre-date challenger freeze")

    analysis = _analyze_legacy_source(connection, experiment=experiment)
    interval = paired_bootstrap_mean_delta(
        analysis.paired_net_pnl,
        seed=derive_seed(
            experiment.experiment_id,
            analysis.challenger_semantic_sha256,
            "ordinary-oos-paired-bootstrap-v1",
        ),
    )
    independent_confirmation_present = any(
        item.role in {EvidenceRole.FRESH_HOLDOUT, EvidenceRole.PROSPECTIVE_PAPER}
        for item in analysis.evidence_manifest
    )
    comparison = compare_policies(
        economic_interval=interval,
        champion_log_loss=analysis.champion_metrics.calibrated_log_loss,
        challenger_log_loss=analysis.challenger_metrics.calibrated_log_loss,
        champion_brier=analysis.champion_metrics.calibrated_brier,
        challenger_brier=analysis.challenger_metrics.calibrated_brier,
        independent_confirmation_present=independent_confirmation_present,
        integrity_ok=True,
    )
    comparison_payload = {
        **asdict(comparison),
        "independent_confirmation_present": independent_confirmation_present,
        "integrity_ok": True,
        "bootstrap_seed_scope": "ordinary-oos-paired-bootstrap-v1",
    }
    return ImprovementEvaluationReport.build(
        evaluation_version=EVALUATION_VERSION,
        experiment_id=experiment.experiment_id,
        challenger_id=derive_id(
            "phase13-spread",
            analysis.challenger_semantic_sha256,
        ),
        challenger_semantic_sha256=analysis.challenger_semantic_sha256,
        challenger_config=analysis.challenger_config,
        evidence_manifest=analysis.evidence_manifest,
        champion_metrics=analysis.champion_metrics,
        challenger_metrics=analysis.challenger_metrics,
        comparison=comparison_payload,
        promotion_eligible=comparison.promotion_eligible,
        ineligibility_reasons=comparison.ineligibility_reasons,
        created_at=created_at,
    )
