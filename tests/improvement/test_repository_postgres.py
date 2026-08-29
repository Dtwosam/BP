from __future__ import annotations

import importlib
import os
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, delete

from bp_engine.improvement.models import (
    DECISION_VERSION,
    EVALUATION_VERSION,
    EXPERIMENT_VERSION,
    ChampionRef,
    ChangeFamily,
    EvidenceItem,
    EvidenceRole,
    ImprovementEvaluationReport,
    ImprovementExperimentSpec,
    ImprovementPromotionDecision,
    PolicyMetrics,
    PromotionDecision,
)
from bp_engine.storage import schema

DATABASE_URL = os.getenv("BP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BP_TEST_DATABASE_URL is required for PostgreSQL integration coverage",
)


def _champion() -> ChampionRef:
    return ChampionRef(
        calibration_run_id="phase9-300-c9f0e00eb7836af08008c66909f8f179",
        calibration_semantic_sha256="a" * 64,
        backtest_run_id="phase8-300-example",
        backtest_semantic_sha256="b" * 64,
        training_run_id="phase7-300-example",
        training_semantic_sha256="c" * 64,
    )


def _experiment(*, hypothesis: str = "A spread guard improves executable economics.") -> ImprovementExperimentSpec:
    return ImprovementExperimentSpec.build(
        experiment_version=EXPERIMENT_VERSION,
        hypothesis=hypothesis,
        horizon_seconds=300,
        change_family=ChangeFamily.ABSTENTION,
        champion=_champion(),
        challenger={"kind": "spread_guard_v1", "grid": [0.02, 0.04, None]},
        source_versions={
            "dataset": "supervised-core-v1",
            "feature": "core-v1",
            "label": "official-outcome-v1",
        },
        research_start=datetime(2026, 8, 24, tzinfo=UTC),
        research_end=datetime(2026, 8, 25, tzinfo=UTC),
        selection_policy={"allowed_roles": ["development_validation"]},
        confirmation_policy={"allowed_roles": ["fresh_holdout", "prospective_paper"]},
        cost_assumptions={"fee_rate": 0.07, "slippage_buffer": 0.01},
        primary_metric="net_pnl_delta",
        guardrail_metrics=("calibrated_log_loss", "calibrated_brier"),
        legacy_confirmation_identifiers=("legacy-final-1",),
        created_at=datetime(2026, 8, 29, 8, 0, tzinfo=UTC),
    )


def _metrics(*, total_net_pnl: float) -> PolicyMetrics:
    return PolicyMetrics(
        calibrated_log_loss=0.4,
        calibrated_brier=0.15,
        ece=0.03,
        accuracy=0.8,
        trade_count=4,
        trade_coverage=0.4,
        total_net_pnl=total_net_pnl,
        mean_net_pnl_per_trade=total_net_pnl / 4,
        max_drawdown=0.1,
        max_losing_streak=1,
        unresolved_count=0,
        missing_unexecutable_count=0,
    )


def _evaluation(
    experiment: ImprovementExperimentSpec,
    *,
    identifier: str = "fresh-condition-1",
    role: EvidenceRole = EvidenceRole.FRESH_HOLDOUT,
    created_at: datetime | None = None,
) -> ImprovementEvaluationReport:
    created_at = created_at or datetime(2026, 8, 31, 8, 0, tzinfo=UTC)
    return ImprovementEvaluationReport.build(
        evaluation_version=EVALUATION_VERSION,
        experiment_id=experiment.experiment_id,
        challenger_id="spread-guard-v1",
        challenger_semantic_sha256="d" * 64,
        challenger_config={"max_spread": 0.04},
        evidence_manifest=(
            EvidenceItem(
                identifier=identifier,
                role=role,
                condition_id=identifier,
                prediction_id=None,
                observed_at=datetime(2026, 8, 30, 8, 0, tzinfo=UTC),
                resolved=True,
                net_pnl=0.10,
            ),
        ),
        champion_metrics=_metrics(total_net_pnl=0.05),
        challenger_metrics=_metrics(total_net_pnl=0.15),
        comparison={"mean_delta": 0.10, "lower_95": 0.01, "upper_95": 0.20},
        promotion_eligible=True,
        ineligibility_reasons=(),
        created_at=created_at,
    )


def _decision(
    evaluation: ImprovementEvaluationReport,
) -> ImprovementPromotionDecision:
    return ImprovementPromotionDecision.build(
        decision_version=DECISION_VERSION,
        evaluation_id=evaluation.evaluation_id,
        experiment_id=evaluation.experiment_id,
        decision=PromotionDecision.KEEP_CHAMPION,
        rationale="The immutable evaluation is recorded; keep the accepted champion.",
        resulting_champion=_champion(),
        created_at=datetime(2026, 9, 1, 8, 0, tzinfo=UTC),
    )


def _setup():
    assert DATABASE_URL is not None
    module = importlib.import_module("bp_engine.improvement.repository")
    engine = create_engine(DATABASE_URL)
    schema.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(delete(schema.improvement_promotion_decisions))
        connection.execute(delete(schema.improvement_evaluations))
        connection.execute(delete(schema.improvement_experiments))
    return module, engine


def test_experiment_repository_is_append_only_and_idempotent() -> None:
    module, engine = _setup()
    repo = module.ImprovementExperimentRepository()
    experiment = _experiment()

    with engine.begin() as connection:
        first = repo.store(connection, experiment)
        second = repo.store(connection, experiment)

        assert first.created is True and first.existing is False
        assert second.created is False and second.existing is True
        stored = repo.get(connection, experiment.experiment_id)
        assert stored is not None
        assert stored["semantic_sha256"] == experiment.semantic_sha256

        with pytest.raises(module.ImprovementExperimentConflict):
            repo.store(
                connection,
                replace(experiment, hypothesis="Changed semantics under the same immutable id."),
            )


def test_evaluation_repository_is_append_only_and_idempotent() -> None:
    module, engine = _setup()
    experiment_repo = module.ImprovementExperimentRepository()
    repo = module.ImprovementEvaluationRepository()
    experiment = _experiment()
    evaluation = _evaluation(experiment)

    with engine.begin() as connection:
        experiment_repo.store(connection, experiment)
        first = repo.store(connection, evaluation)
        second = repo.store(connection, evaluation)

        assert first.created is True and first.existing is False
        assert second.created is False and second.existing is True
        stored = repo.get(connection, evaluation.evaluation_id)
        assert stored is not None
        assert stored["semantic_sha256"] == evaluation.semantic_sha256

        with pytest.raises(module.ImprovementEvaluationConflict):
            repo.store(
                connection,
                replace(evaluation, challenger_config={"max_spread": 0.08}),
            )


def test_promotion_decision_repository_is_append_only_and_idempotent() -> None:
    module, engine = _setup()
    experiment_repo = module.ImprovementExperimentRepository()
    evaluation_repo = module.ImprovementEvaluationRepository()
    repo = module.ImprovementPromotionDecisionRepository()
    experiment = _experiment()
    evaluation = _evaluation(experiment)
    decision = _decision(evaluation)

    with engine.begin() as connection:
        experiment_repo.store(connection, experiment)
        evaluation_repo.store(connection, evaluation)
        first = repo.store(connection, decision)
        second = repo.store(connection, decision)

        assert first.created is True and first.existing is False
        assert second.created is False and second.existing is True
        stored = repo.get(connection, decision.decision_id)
        assert stored is not None
        assert stored["semantic_sha256"] == decision.semantic_sha256

        with pytest.raises(module.ImprovementPromotionDecisionConflict):
            repo.store(
                connection,
                replace(decision, rationale="Changed rationale under the same immutable id."),
            )


def test_fresh_confirmation_overlap_is_detected_only_across_other_experiments() -> None:
    module, engine = _setup()
    experiment_repo = module.ImprovementExperimentRepository()
    evaluation_repo = module.ImprovementEvaluationRepository()
    first_experiment = _experiment()
    second_experiment = _experiment(hypothesis="A second independent hypothesis.")
    first_evaluation = _evaluation(first_experiment, identifier="fresh-shared")
    ordinary_evaluation = _evaluation(
        first_experiment,
        identifier="ordinary-shared",
        role=EvidenceRole.ORDINARY_OOS,
        created_at=datetime(2026, 8, 31, 9, 0, tzinfo=UTC),
    )

    with engine.begin() as connection:
        experiment_repo.store(connection, first_experiment)
        experiment_repo.store(connection, second_experiment)
        evaluation_repo.store(connection, first_evaluation)
        evaluation_repo.store(connection, ordinary_evaluation)

        overlap = module.confirmation_identifiers_used_by_other_experiments(
            connection,
            experiment_id=second_experiment.experiment_id,
            identifiers={"fresh-shared", "ordinary-shared", "unused"},
        )
        same_experiment = module.confirmation_identifiers_used_by_other_experiments(
            connection,
            experiment_id=first_experiment.experiment_id,
            identifiers={"fresh-shared"},
        )

        assert overlap == {"fresh-shared"}
        assert same_experiment == set()
