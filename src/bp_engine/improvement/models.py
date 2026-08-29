from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from bp_engine.improvement.hashing import derive_id, semantic_sha256

EXPERIMENT_VERSION = "improvement-experiment-v1"
EVALUATION_VERSION = "improvement-evaluation-v1"
DECISION_VERSION = "improvement-decision-v1"


class ChangeFamily(StrEnum):
    FEATURE = "feature"
    MODEL = "model"
    CALIBRATION = "calibration"
    TIMING = "timing"
    ABSTENTION = "abstention"
    COST_ASSUMPTION = "cost_assumption"


class EvidenceRole(StrEnum):
    DEVELOPMENT_TRAIN = "development_train"
    DEVELOPMENT_VALIDATION = "development_validation"
    ORDINARY_OOS = "ordinary_oos"
    FRESH_HOLDOUT = "fresh_holdout"
    PROSPECTIVE_PAPER = "prospective_paper"


class PromotionDecision(StrEnum):
    REJECT_CHALLENGER = "reject_challenger"
    KEEP_CHAMPION = "keep_champion"
    PROMOTE_CHALLENGER = "promote_challenger"


def _require_aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _require_sha256(name: str, value: str) -> None:
    if len(value) != 64:
        raise ValueError(f"{name} must be a 64-character SHA-256 digest")


def _require_nonblank(name: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must not be blank")
    return normalized


def _require_finite(name: str, value: float) -> None:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")


@dataclass(frozen=True)
class ChampionRef:
    calibration_run_id: str
    calibration_semantic_sha256: str
    backtest_run_id: str
    backtest_semantic_sha256: str
    training_run_id: str
    training_semantic_sha256: str

    def __post_init__(self) -> None:
        for name in ("calibration_run_id", "backtest_run_id", "training_run_id"):
            _require_nonblank(name, getattr(self, name))
        for name in (
            "calibration_semantic_sha256",
            "backtest_semantic_sha256",
            "training_semantic_sha256",
        ):
            _require_sha256(name, getattr(self, name))


@dataclass(frozen=True)
class EvidenceItem:
    identifier: str
    role: EvidenceRole
    condition_id: str
    prediction_id: str | None
    observed_at: datetime
    resolved: bool
    net_pnl: float | None

    def __post_init__(self) -> None:
        _require_nonblank("identifier", self.identifier)
        _require_nonblank("condition_id", self.condition_id)
        if self.prediction_id is not None:
            _require_nonblank("prediction_id", self.prediction_id)
        _require_aware("observed_at", self.observed_at)
        if self.net_pnl is not None:
            _require_finite("net_pnl", self.net_pnl)


@dataclass(frozen=True)
class PolicyMetrics:
    calibrated_log_loss: float
    calibrated_brier: float
    ece: float
    accuracy: float
    trade_count: int
    trade_coverage: float
    total_net_pnl: float
    mean_net_pnl_per_trade: float
    max_drawdown: float
    max_losing_streak: int
    unresolved_count: int
    missing_unexecutable_count: int

    def __post_init__(self) -> None:
        for name in (
            "calibrated_log_loss",
            "calibrated_brier",
            "ece",
            "accuracy",
            "trade_coverage",
            "total_net_pnl",
            "mean_net_pnl_per_trade",
            "max_drawdown",
        ):
            _require_finite(name, getattr(self, name))
        for name in ("calibrated_brier", "ece", "accuracy", "trade_coverage"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.calibrated_log_loss < 0.0:
            raise ValueError("calibrated_log_loss must be nonnegative")
        if self.max_drawdown < 0.0:
            raise ValueError("max_drawdown must be nonnegative")
        for name in (
            "trade_count",
            "max_losing_streak",
            "unresolved_count",
            "missing_unexecutable_count",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be nonnegative")


@dataclass(frozen=True)
class ImprovementExperimentSpec:
    experiment_id: str
    experiment_version: str
    hypothesis: str
    horizon_seconds: int
    change_family: ChangeFamily
    champion: ChampionRef
    challenger: dict[str, Any]
    source_versions: dict[str, str]
    research_start: datetime
    research_end: datetime
    selection_policy: dict[str, Any]
    confirmation_policy: dict[str, Any]
    cost_assumptions: dict[str, Any]
    primary_metric: str
    guardrail_metrics: tuple[str, ...]
    legacy_confirmation_identifiers: tuple[str, ...]
    semantic_sha256: str
    created_at: datetime

    @classmethod
    def build(
        cls,
        *,
        experiment_version: str,
        hypothesis: str,
        horizon_seconds: int,
        change_family: ChangeFamily,
        champion: ChampionRef,
        challenger: dict[str, Any],
        source_versions: dict[str, str],
        research_start: datetime,
        research_end: datetime,
        selection_policy: dict[str, Any],
        confirmation_policy: dict[str, Any],
        cost_assumptions: dict[str, Any],
        primary_metric: str,
        guardrail_metrics: tuple[str, ...],
        legacy_confirmation_identifiers: tuple[str, ...],
        created_at: datetime,
    ) -> ImprovementExperimentSpec:
        if experiment_version != EXPERIMENT_VERSION:
            raise ValueError(f"experiment_version must be {EXPERIMENT_VERSION}")
        normalized_hypothesis = _require_nonblank("hypothesis", hypothesis)
        if horizon_seconds <= 0:
            raise ValueError("horizon_seconds must be positive")
        _require_aware("research_start", research_start)
        _require_aware("research_end", research_end)
        _require_aware("created_at", created_at)
        if research_end <= research_start:
            raise ValueError("research_start must be before research_end")
        normalized_primary_metric = _require_nonblank("primary_metric", primary_metric)
        if not guardrail_metrics:
            raise ValueError("guardrail_metrics must not be empty")
        normalized_guardrails = tuple(
            _require_nonblank("guardrail_metrics", metric) for metric in guardrail_metrics
        )
        normalized_legacy_ids = tuple(sorted(set(legacy_confirmation_identifiers)))
        semantic_payload = {
            "experiment_version": experiment_version,
            "hypothesis": normalized_hypothesis,
            "horizon_seconds": horizon_seconds,
            "change_family": change_family,
            "champion": champion,
            "challenger": dict(challenger),
            "source_versions": dict(source_versions),
            "research_start": research_start,
            "research_end": research_end,
            "selection_policy": dict(selection_policy),
            "confirmation_policy": dict(confirmation_policy),
            "cost_assumptions": dict(cost_assumptions),
            "primary_metric": normalized_primary_metric,
            "guardrail_metrics": normalized_guardrails,
            "legacy_confirmation_identifiers": normalized_legacy_ids,
        }
        digest = semantic_sha256(semantic_payload)
        return cls(
            experiment_id=derive_id("phase13-exp", digest),
            experiment_version=experiment_version,
            hypothesis=normalized_hypothesis,
            horizon_seconds=horizon_seconds,
            change_family=change_family,
            champion=champion,
            challenger=dict(challenger),
            source_versions=dict(source_versions),
            research_start=research_start,
            research_end=research_end,
            selection_policy=dict(selection_policy),
            confirmation_policy=dict(confirmation_policy),
            cost_assumptions=dict(cost_assumptions),
            primary_metric=normalized_primary_metric,
            guardrail_metrics=normalized_guardrails,
            legacy_confirmation_identifiers=normalized_legacy_ids,
            semantic_sha256=digest,
            created_at=created_at,
        )


@dataclass(frozen=True)
class ImprovementEvaluationReport:
    evaluation_id: str
    evaluation_version: str
    experiment_id: str
    challenger_id: str
    challenger_semantic_sha256: str
    challenger_config: dict[str, Any]
    evidence_manifest: tuple[EvidenceItem, ...]
    champion_metrics: PolicyMetrics
    challenger_metrics: PolicyMetrics
    comparison: dict[str, Any]
    promotion_eligible: bool
    ineligibility_reasons: tuple[str, ...]
    semantic_sha256: str
    created_at: datetime

    @classmethod
    def build(
        cls,
        *,
        evaluation_version: str,
        experiment_id: str,
        challenger_id: str,
        challenger_semantic_sha256: str,
        challenger_config: dict[str, Any],
        evidence_manifest: tuple[EvidenceItem, ...],
        champion_metrics: PolicyMetrics,
        challenger_metrics: PolicyMetrics,
        comparison: dict[str, Any],
        promotion_eligible: bool,
        ineligibility_reasons: tuple[str, ...],
        created_at: datetime,
    ) -> ImprovementEvaluationReport:
        if evaluation_version != EVALUATION_VERSION:
            raise ValueError(f"evaluation_version must be {EVALUATION_VERSION}")
        normalized_experiment_id = _require_nonblank("experiment_id", experiment_id)
        normalized_challenger_id = _require_nonblank("challenger_id", challenger_id)
        _require_sha256("challenger_semantic_sha256", challenger_semantic_sha256)
        _require_aware("created_at", created_at)
        normalized_evidence = tuple(
            sorted(evidence_manifest, key=lambda item: (item.role.value, item.identifier))
        )
        normalized_reasons = tuple(
            sorted(
                {
                    _require_nonblank("ineligibility_reasons", reason)
                    for reason in ineligibility_reasons
                }
            )
        )
        if promotion_eligible and normalized_reasons:
            raise ValueError(
                "promotion_eligible evaluation cannot contain ineligibility_reasons"
            )
        semantic_payload = {
            "evaluation_version": evaluation_version,
            "experiment_id": normalized_experiment_id,
            "challenger_id": normalized_challenger_id,
            "challenger_semantic_sha256": challenger_semantic_sha256,
            "challenger_config": dict(challenger_config),
            "evidence_manifest": normalized_evidence,
            "champion_metrics": champion_metrics,
            "challenger_metrics": challenger_metrics,
            "comparison": dict(comparison),
            "promotion_eligible": promotion_eligible,
            "ineligibility_reasons": normalized_reasons,
        }
        digest = semantic_sha256(semantic_payload)
        return cls(
            evaluation_id=derive_id("phase13-eval", digest),
            evaluation_version=evaluation_version,
            experiment_id=normalized_experiment_id,
            challenger_id=normalized_challenger_id,
            challenger_semantic_sha256=challenger_semantic_sha256,
            challenger_config=dict(challenger_config),
            evidence_manifest=normalized_evidence,
            champion_metrics=champion_metrics,
            challenger_metrics=challenger_metrics,
            comparison=dict(comparison),
            promotion_eligible=promotion_eligible,
            ineligibility_reasons=normalized_reasons,
            semantic_sha256=digest,
            created_at=created_at,
        )


@dataclass(frozen=True)
class ImprovementPromotionDecision:
    decision_id: str
    decision_version: str
    evaluation_id: str
    experiment_id: str
    decision: PromotionDecision
    rationale: str
    resulting_champion: ChampionRef
    semantic_sha256: str
    created_at: datetime

    @classmethod
    def build(
        cls,
        *,
        decision_version: str,
        evaluation_id: str,
        experiment_id: str,
        decision: PromotionDecision,
        rationale: str,
        resulting_champion: ChampionRef,
        created_at: datetime,
    ) -> ImprovementPromotionDecision:
        if decision_version != DECISION_VERSION:
            raise ValueError(f"decision_version must be {DECISION_VERSION}")
        normalized_evaluation_id = _require_nonblank("evaluation_id", evaluation_id)
        normalized_experiment_id = _require_nonblank("experiment_id", experiment_id)
        normalized_rationale = _require_nonblank("rationale", rationale)
        _require_aware("created_at", created_at)
        semantic_payload = {
            "decision_version": decision_version,
            "evaluation_id": normalized_evaluation_id,
            "experiment_id": normalized_experiment_id,
            "decision": decision,
            "rationale": normalized_rationale,
            "resulting_champion": resulting_champion,
        }
        digest = semantic_sha256(semantic_payload)
        return cls(
            decision_id=derive_id("phase13-decision", digest),
            decision_version=decision_version,
            evaluation_id=normalized_evaluation_id,
            experiment_id=normalized_experiment_id,
            decision=decision,
            rationale=normalized_rationale,
            resulting_champion=resulting_champion,
            semantic_sha256=digest,
            created_at=created_at,
        )
