from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.engine import Connection

from bp_engine.improvement.hashing import canonical_payload
from bp_engine.improvement.models import (
    ImprovementEvaluationReport,
    ImprovementExperimentSpec,
    ImprovementPromotionDecision,
)
from bp_engine.storage.improvement_schema import (
    improvement_evaluations,
    improvement_experiments,
    improvement_promotion_decisions,
)

_CONFIRMATION_ROLES = {"fresh_holdout", "prospective_paper"}


class ImprovementExperimentConflict(ValueError):
    """Raised when immutable experiment identity and supplied semantics disagree."""


class ImprovementEvaluationConflict(ValueError):
    """Raised when immutable evaluation identity and supplied semantics disagree."""


class ImprovementPromotionDecisionConflict(ValueError):
    """Raised when immutable decision identity and supplied semantics disagree."""


@dataclass(frozen=True)
class ImprovementStoreResult:
    created: bool
    existing: bool


def _experiment_payload(record: ImprovementExperimentSpec) -> dict[str, Any]:
    payload = {
        "experiment_version": record.experiment_version,
        "hypothesis": record.hypothesis,
        "horizon_seconds": record.horizon_seconds,
        "change_family": record.change_family,
        "champion": record.champion,
        "challenger": record.challenger,
        "source_versions": record.source_versions,
        "research_start": record.research_start,
        "research_end": record.research_end,
        "selection_policy": record.selection_policy,
        "confirmation_policy": record.confirmation_policy,
        "cost_assumptions": record.cost_assumptions,
        "primary_metric": record.primary_metric,
        "guardrail_metrics": record.guardrail_metrics,
        "legacy_confirmation_identifiers": record.legacy_confirmation_identifiers,
    }
    return canonical_payload(payload)


def _evaluation_payload(record: ImprovementEvaluationReport) -> dict[str, Any]:
    payload = {
        "evaluation_version": record.evaluation_version,
        "experiment_id": record.experiment_id,
        "challenger_id": record.challenger_id,
        "challenger_semantic_sha256": record.challenger_semantic_sha256,
        "challenger_config": record.challenger_config,
        "evidence_manifest": record.evidence_manifest,
        "champion_metrics": record.champion_metrics,
        "challenger_metrics": record.challenger_metrics,
        "comparison": record.comparison,
        "promotion_eligible": record.promotion_eligible,
        "ineligibility_reasons": record.ineligibility_reasons,
    }
    return canonical_payload(payload)


def _decision_payload(record: ImprovementPromotionDecision) -> dict[str, Any]:
    payload = {
        "decision_version": record.decision_version,
        "evaluation_id": record.evaluation_id,
        "experiment_id": record.experiment_id,
        "decision": record.decision,
        "rationale": record.rationale,
        "resulting_champion": record.resulting_champion,
    }
    return canonical_payload(payload)


def _validate_experiment(
    record: ImprovementExperimentSpec,
) -> ImprovementExperimentSpec:
    rebuilt = ImprovementExperimentSpec.build(
        experiment_version=record.experiment_version,
        hypothesis=record.hypothesis,
        horizon_seconds=record.horizon_seconds,
        change_family=record.change_family,
        champion=record.champion,
        challenger=record.challenger,
        source_versions=record.source_versions,
        research_start=record.research_start,
        research_end=record.research_end,
        selection_policy=record.selection_policy,
        confirmation_policy=record.confirmation_policy,
        cost_assumptions=record.cost_assumptions,
        primary_metric=record.primary_metric,
        guardrail_metrics=record.guardrail_metrics,
        legacy_confirmation_identifiers=record.legacy_confirmation_identifiers,
        created_at=record.created_at,
    )
    if (
        rebuilt.experiment_id != record.experiment_id
        or rebuilt.semantic_sha256 != record.semantic_sha256
    ):
        raise ImprovementExperimentConflict(
            f"experiment {record.experiment_id} carries semantics inconsistent "
            "with its immutable id"
        )
    return rebuilt


def _validate_evaluation(
    record: ImprovementEvaluationReport,
) -> ImprovementEvaluationReport:
    rebuilt = ImprovementEvaluationReport.build(
        evaluation_version=record.evaluation_version,
        experiment_id=record.experiment_id,
        challenger_id=record.challenger_id,
        challenger_semantic_sha256=record.challenger_semantic_sha256,
        challenger_config=record.challenger_config,
        evidence_manifest=record.evidence_manifest,
        champion_metrics=record.champion_metrics,
        challenger_metrics=record.challenger_metrics,
        comparison=record.comparison,
        promotion_eligible=record.promotion_eligible,
        ineligibility_reasons=record.ineligibility_reasons,
        created_at=record.created_at,
    )
    if (
        rebuilt.evaluation_id != record.evaluation_id
        or rebuilt.semantic_sha256 != record.semantic_sha256
    ):
        raise ImprovementEvaluationConflict(
            f"evaluation {record.evaluation_id} carries semantics inconsistent "
            "with its immutable id"
        )
    return rebuilt


def _validate_decision(
    record: ImprovementPromotionDecision,
) -> ImprovementPromotionDecision:
    rebuilt = ImprovementPromotionDecision.build(
        decision_version=record.decision_version,
        evaluation_id=record.evaluation_id,
        experiment_id=record.experiment_id,
        decision=record.decision,
        rationale=record.rationale,
        resulting_champion=record.resulting_champion,
        created_at=record.created_at,
    )
    if (
        rebuilt.decision_id != record.decision_id
        or rebuilt.semantic_sha256 != record.semantic_sha256
    ):
        raise ImprovementPromotionDecisionConflict(
            f"decision {record.decision_id} carries semantics inconsistent with its immutable id"
        )
    return rebuilt


class ImprovementExperimentRepository:
    def get(self, connection: Connection, experiment_id: str) -> dict[str, Any] | None:
        row = connection.execute(
            select(improvement_experiments).where(
                improvement_experiments.c.experiment_id == experiment_id
            )
        ).mappings().one_or_none()
        return None if row is None else dict(row)

    def store(
        self,
        connection: Connection,
        record: ImprovementExperimentSpec,
    ) -> ImprovementStoreResult:
        rebuilt = _validate_experiment(record)
        payload = _experiment_payload(rebuilt)
        existing = self.get(connection, rebuilt.experiment_id)
        if existing is not None:
            if (
                existing["semantic_sha256"] == rebuilt.semantic_sha256
                and existing["spec"] == payload
            ):
                return ImprovementStoreResult(created=False, existing=True)
            raise ImprovementExperimentConflict(
                f"experiment {rebuilt.experiment_id} already exists with different semantics"
            )

        connection.execute(
            insert(improvement_experiments).values(
                experiment_id=rebuilt.experiment_id,
                experiment_version=rebuilt.experiment_version,
                horizon_seconds=rebuilt.horizon_seconds,
                change_family=rebuilt.change_family.value,
                champion_calibration_run_id=rebuilt.champion.calibration_run_id,
                champion_calibration_semantic_sha256=(
                    rebuilt.champion.calibration_semantic_sha256
                ),
                hypothesis=rebuilt.hypothesis,
                spec=payload,
                semantic_sha256=rebuilt.semantic_sha256,
                created_at=rebuilt.created_at,
            )
        )
        return ImprovementStoreResult(created=True, existing=False)


class ImprovementEvaluationRepository:
    def get(self, connection: Connection, evaluation_id: str) -> dict[str, Any] | None:
        row = connection.execute(
            select(improvement_evaluations).where(
                improvement_evaluations.c.evaluation_id == evaluation_id
            )
        ).mappings().one_or_none()
        return None if row is None else dict(row)

    def store(
        self,
        connection: Connection,
        record: ImprovementEvaluationReport,
    ) -> ImprovementStoreResult:
        rebuilt = _validate_evaluation(record)
        payload = _evaluation_payload(rebuilt)
        existing = self.get(connection, rebuilt.evaluation_id)
        if existing is not None:
            if (
                existing["semantic_sha256"] == rebuilt.semantic_sha256
                and existing["report"] == payload
            ):
                return ImprovementStoreResult(created=False, existing=True)
            raise ImprovementEvaluationConflict(
                f"evaluation {rebuilt.evaluation_id} already exists with different semantics"
            )

        connection.execute(
            insert(improvement_evaluations).values(
                evaluation_id=rebuilt.evaluation_id,
                evaluation_version=rebuilt.evaluation_version,
                experiment_id=rebuilt.experiment_id,
                challenger_id=rebuilt.challenger_id,
                challenger_semantic_sha256=rebuilt.challenger_semantic_sha256,
                evidence_manifest=canonical_payload(rebuilt.evidence_manifest),
                champion_metrics=canonical_payload(rebuilt.champion_metrics),
                challenger_metrics=canonical_payload(rebuilt.challenger_metrics),
                comparison=canonical_payload(rebuilt.comparison),
                promotion_eligible=rebuilt.promotion_eligible,
                ineligibility_reasons=canonical_payload(rebuilt.ineligibility_reasons),
                report=payload,
                semantic_sha256=rebuilt.semantic_sha256,
                created_at=rebuilt.created_at,
            )
        )
        return ImprovementStoreResult(created=True, existing=False)


class ImprovementPromotionDecisionRepository:
    def get(self, connection: Connection, decision_id: str) -> dict[str, Any] | None:
        row = connection.execute(
            select(improvement_promotion_decisions).where(
                improvement_promotion_decisions.c.decision_id == decision_id
            )
        ).mappings().one_or_none()
        return None if row is None else dict(row)

    def store(
        self,
        connection: Connection,
        record: ImprovementPromotionDecision,
    ) -> ImprovementStoreResult:
        rebuilt = _validate_decision(record)
        payload = _decision_payload(rebuilt)
        existing = self.get(connection, rebuilt.decision_id)
        if existing is not None:
            if (
                existing["semantic_sha256"] == rebuilt.semantic_sha256
                and existing["decision_record"] == payload
            ):
                return ImprovementStoreResult(created=False, existing=True)
            raise ImprovementPromotionDecisionConflict(
                f"decision {rebuilt.decision_id} already exists with different semantics"
            )

        connection.execute(
            insert(improvement_promotion_decisions).values(
                decision_id=rebuilt.decision_id,
                decision_version=rebuilt.decision_version,
                evaluation_id=rebuilt.evaluation_id,
                experiment_id=rebuilt.experiment_id,
                decision=rebuilt.decision.value,
                rationale=rebuilt.rationale,
                resulting_champion=canonical_payload(rebuilt.resulting_champion),
                decision_record=payload,
                semantic_sha256=rebuilt.semantic_sha256,
                created_at=rebuilt.created_at,
            )
        )
        return ImprovementStoreResult(created=True, existing=False)


def confirmation_identifiers_used_by_other_experiments(
    connection: Connection,
    *,
    experiment_id: str,
    identifiers: set[str],
) -> set[str]:
    if not identifiers:
        return set()

    rows = connection.execute(
        select(
            improvement_evaluations.c.experiment_id,
            improvement_evaluations.c.evidence_manifest,
        ).where(improvement_evaluations.c.experiment_id != experiment_id)
    ).mappings()

    overlaps: set[str] = set()
    for row in rows:
        for item in row["evidence_manifest"]:
            if (
                item.get("role") in _CONFIRMATION_ROLES
                and item.get("identifier") in identifiers
            ):
                overlaps.add(item["identifier"])
    return overlaps
