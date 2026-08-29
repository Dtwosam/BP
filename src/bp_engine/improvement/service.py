from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Connection

from bp_engine.improvement.evidence import EvidenceIntegrityError, validate_evidence_manifest
from bp_engine.improvement.models import (
    DECISION_VERSION,
    ChampionRef,
    ChangeFamily,
    ImprovementEvaluationReport,
    ImprovementExperimentSpec,
    ImprovementPromotionDecision,
    PromotionDecision,
)
from bp_engine.improvement.repository import (
    ImprovementEvaluationRepository,
    ImprovementExperimentRepository,
    ImprovementPromotionDecisionRepository,
    ImprovementStoreResult,
    confirmation_identifiers_used_by_other_experiments,
)
from bp_engine.storage.improvement_schema import (
    improvement_evaluations,
    improvement_promotion_decisions,
)

__all__ = [
    "EvidenceIntegrityError",
    "ImprovementDecisionError",
    "ImprovementServiceError",
    "evaluate_experiment",
    "get_experiment_report",
    "record_decision",
    "register_experiment",
    "store_evaluation",
]


class ImprovementServiceError(ValueError):
    """Raised when improvement orchestration invariants are violated."""


class ImprovementDecisionError(ImprovementServiceError):
    """Raised when a deliberate decision is invalid for its evaluation."""


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _experiment_from_row(row: dict[str, Any]) -> ImprovementExperimentSpec:
    spec = row["spec"]
    champion = ChampionRef(**spec["champion"])
    experiment = ImprovementExperimentSpec.build(
        experiment_version=spec["experiment_version"],
        hypothesis=spec["hypothesis"],
        horizon_seconds=spec["horizon_seconds"],
        change_family=ChangeFamily(spec["change_family"]),
        champion=champion,
        challenger=spec["challenger"],
        source_versions=spec["source_versions"],
        research_start=_parse_datetime(spec["research_start"]),
        research_end=_parse_datetime(spec["research_end"]),
        selection_policy=spec["selection_policy"],
        confirmation_policy=spec["confirmation_policy"],
        cost_assumptions=spec["cost_assumptions"],
        primary_metric=spec["primary_metric"],
        guardrail_metrics=tuple(spec["guardrail_metrics"]),
        legacy_confirmation_identifiers=tuple(spec["legacy_confirmation_identifiers"]),
        created_at=row["created_at"],
    )
    if (
        experiment.experiment_id != row["experiment_id"]
        or experiment.semantic_sha256 != row["semantic_sha256"]
    ):
        raise ImprovementServiceError(
            f"experiment {row['experiment_id']} failed immutable reconstruction"
        )
    return experiment


def _load_experiment(
    connection: Connection,
    experiment_id: str,
) -> ImprovementExperimentSpec:
    row = ImprovementExperimentRepository().get(connection, experiment_id)
    if row is None:
        raise ImprovementServiceError(f"experiment {experiment_id} is not registered")
    return _experiment_from_row(row)


def register_experiment(
    connection: Connection,
    spec: ImprovementExperimentSpec,
) -> ImprovementStoreResult:
    return ImprovementExperimentRepository().store(connection, spec)


def store_evaluation(
    connection: Connection,
    report: ImprovementEvaluationReport,
) -> ImprovementStoreResult:
    experiment = _load_experiment(connection, report.experiment_id)
    if report.created_at < experiment.created_at:
        raise ImprovementServiceError(
            f"evaluation {report.evaluation_id} was created before experiment freeze"
        )

    confirmation_identifiers = {
        item.identifier
        for item in report.evidence_manifest
        if item.role.value in {"fresh_holdout", "prospective_paper"}
    }
    prior_identifiers = confirmation_identifiers_used_by_other_experiments(
        connection,
        experiment_id=experiment.experiment_id,
        identifiers=confirmation_identifiers,
    )
    validate_evidence_manifest(
        experiment=experiment,
        evidence=report.evidence_manifest,
        prior_confirmation_identifiers=prior_identifiers,
    )
    return ImprovementEvaluationRepository().store(connection, report)


def evaluate_experiment(
    connection: Connection,
    *,
    experiment_id: str,
    created_at: datetime,
) -> ImprovementEvaluationReport:
    experiment = _load_experiment(connection, experiment_id)
    challenger_kind = experiment.challenger.get("kind")
    if challenger_kind != "spread_guard_v1":
        raise ImprovementServiceError(
            f"unsupported challenger kind for evaluation: {challenger_kind!r}"
        )

    from bp_engine.improvement.challenger import evaluate_spread_guard_challenger

    report = evaluate_spread_guard_challenger(
        connection,
        experiment_id=experiment_id,
        created_at=created_at,
    )
    store_evaluation(connection, report)
    return report


def record_decision(
    connection: Connection,
    *,
    evaluation_id: str,
    decision: PromotionDecision,
    rationale: str,
    created_at: datetime,
) -> ImprovementPromotionDecision:
    evaluation = ImprovementEvaluationRepository().get(connection, evaluation_id)
    if evaluation is None:
        raise ImprovementDecisionError(f"evaluation {evaluation_id} does not exist")
    if not rationale.strip():
        raise ImprovementDecisionError("rationale must not be blank")
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise ImprovementDecisionError("decision created_at must be timezone-aware")
    if created_at < evaluation["created_at"]:
        raise ImprovementDecisionError(
            f"decision for evaluation {evaluation_id} was created before evaluation"
        )
    if decision is PromotionDecision.PROMOTE_CHALLENGER and not evaluation["promotion_eligible"]:
        raise ImprovementDecisionError(
            f"evaluation {evaluation_id} is not promotion eligible"
        )

    experiment = _load_experiment(connection, evaluation["experiment_id"])
    record = ImprovementPromotionDecision.build(
        decision_version=DECISION_VERSION,
        evaluation_id=evaluation_id,
        experiment_id=experiment.experiment_id,
        decision=decision,
        rationale=rationale,
        resulting_champion=experiment.champion,
        created_at=created_at,
    )
    ImprovementPromotionDecisionRepository().store(connection, record)
    return record


def get_experiment_report(
    connection: Connection,
    experiment_id: str,
) -> dict[str, Any]:
    experiment = ImprovementExperimentRepository().get(connection, experiment_id)
    if experiment is None:
        raise ImprovementServiceError(f"experiment {experiment_id} is not registered")

    evaluations = connection.execute(
        select(improvement_evaluations)
        .where(improvement_evaluations.c.experiment_id == experiment_id)
        .order_by(improvement_evaluations.c.created_at, improvement_evaluations.c.id)
    ).mappings()
    decisions = connection.execute(
        select(improvement_promotion_decisions)
        .where(improvement_promotion_decisions.c.experiment_id == experiment_id)
        .order_by(
            improvement_promotion_decisions.c.created_at,
            improvement_promotion_decisions.c.id,
        )
    ).mappings()
    return {
        "experiment": experiment,
        "evaluations": [dict(row) for row in evaluations],
        "decisions": [dict(row) for row in decisions],
    }
