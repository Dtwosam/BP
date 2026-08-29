from __future__ import annotations

import importlib
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, delete

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
    PromotionDecision,
)
from bp_engine.storage import schema

DATABASE_URL = os.getenv("BP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BP_TEST_DATABASE_URL is required for PostgreSQL integration coverage",
)

FREEZE_AT = datetime(2026, 8, 29, 8, 0, tzinfo=UTC)
EVALUATED_AT = datetime(2026, 8, 31, 8, 0, tzinfo=UTC)
DECIDED_AT = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)


def _champion() -> ChampionRef:
    return ChampionRef(
        calibration_run_id="phase9-300-c9f0e00eb7836af08008c66909f8f179",
        calibration_semantic_sha256="a" * 64,
        backtest_run_id="phase8-300-example",
        backtest_semantic_sha256="b" * 64,
        training_run_id="phase7-300-example",
        training_semantic_sha256="c" * 64,
    )


def _experiment(*, hypothesis: str = "A spread guard improves executable economics."):
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
        created_at=FREEZE_AT,
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
    promotion_eligible: bool = True,
    created_at: datetime = EVALUATED_AT,
) -> ImprovementEvaluationReport:
    reasons = () if promotion_eligible else ("economic_uncertainty_not_positive",)
    prediction_id = "prediction-1" if role is EvidenceRole.PROSPECTIVE_PAPER else None
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
                prediction_id=prediction_id,
                observed_at=FREEZE_AT + timedelta(days=1),
                resolved=True,
                net_pnl=0.10,
            ),
        ),
        champion_metrics=_metrics(total_net_pnl=0.05),
        challenger_metrics=_metrics(total_net_pnl=0.15),
        comparison={"mean_delta": 0.10, "lower_95": 0.01, "upper_95": 0.20},
        promotion_eligible=promotion_eligible,
        ineligibility_reasons=reasons,
        created_at=created_at,
    )


def _setup():
    assert DATABASE_URL is not None
    service = importlib.import_module("bp_engine.improvement.service")
    engine = create_engine(DATABASE_URL)
    schema.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(delete(schema.improvement_promotion_decisions))
        connection.execute(delete(schema.improvement_evaluations))
        connection.execute(delete(schema.improvement_experiments))
    return service, engine


def test_register_experiment_is_idempotent() -> None:
    service, engine = _setup()
    experiment = _experiment()

    with engine.begin() as connection:
        first = service.register_experiment(connection, experiment)
        second = service.register_experiment(connection, experiment)

    assert first.created is True and first.existing is False
    assert second.created is False and second.existing is True


def test_store_evaluation_requires_registered_experiment() -> None:
    service, engine = _setup()
    evaluation = _evaluation(_experiment())

    with engine.begin() as connection:
        with pytest.raises(service.ImprovementServiceError, match="experiment"):
            service.store_evaluation(connection, evaluation)


def test_store_evaluation_rejects_timestamp_before_experiment() -> None:
    service, engine = _setup()
    experiment = _experiment()
    evaluation = _evaluation(
        experiment,
        created_at=FREEZE_AT - timedelta(seconds=1),
    )

    with engine.begin() as connection:
        service.register_experiment(connection, experiment)
        with pytest.raises(service.ImprovementServiceError, match="before experiment"):
            service.store_evaluation(connection, evaluation)


def test_store_evaluation_rejects_confirmation_reused_by_other_experiment() -> None:
    service, engine = _setup()
    first_experiment = _experiment()
    second_experiment = _experiment(hypothesis="A second independent hypothesis.")
    first_evaluation = _evaluation(first_experiment, identifier="fresh-shared")
    second_evaluation = _evaluation(second_experiment, identifier="fresh-shared")

    with engine.begin() as connection:
        service.register_experiment(connection, first_experiment)
        service.register_experiment(connection, second_experiment)
        service.store_evaluation(connection, first_evaluation)
        with pytest.raises(service.EvidenceIntegrityError, match="already consumed"):
            service.store_evaluation(connection, second_evaluation)


def test_ineligible_evaluation_cannot_promote_challenger() -> None:
    service, engine = _setup()
    experiment = _experiment()
    evaluation = _evaluation(experiment, promotion_eligible=False)

    with engine.begin() as connection:
        service.register_experiment(connection, experiment)
        service.store_evaluation(connection, evaluation)
        with pytest.raises(service.ImprovementDecisionError, match="not promotion eligible"):
            service.record_decision(
                connection,
                evaluation_id=evaluation.evaluation_id,
                decision=PromotionDecision.PROMOTE_CHALLENGER,
                rationale="force it",
                created_at=DECIDED_AT,
            )


def test_keep_champion_is_permitted_after_ineligible_evaluation() -> None:
    service, engine = _setup()
    experiment = _experiment()
    evaluation = _evaluation(experiment, promotion_eligible=False)

    with engine.begin() as connection:
        service.register_experiment(connection, experiment)
        service.store_evaluation(connection, evaluation)
        decision = service.record_decision(
            connection,
            evaluation_id=evaluation.evaluation_id,
            decision=PromotionDecision.KEEP_CHAMPION,
            rationale="Independent confirmation is inconclusive.",
            created_at=DECIDED_AT,
        )

    assert decision.decision is PromotionDecision.KEEP_CHAMPION
    assert decision.resulting_champion == experiment.champion


def test_reject_challenger_is_permitted_after_valid_evaluation() -> None:
    service, engine = _setup()
    experiment = _experiment()
    evaluation = _evaluation(experiment)

    with engine.begin() as connection:
        service.register_experiment(connection, experiment)
        service.store_evaluation(connection, evaluation)
        decision = service.record_decision(
            connection,
            evaluation_id=evaluation.evaluation_id,
            decision=PromotionDecision.REJECT_CHALLENGER,
            rationale="Economic benefit is not operationally compelling.",
            created_at=DECIDED_AT,
        )

    assert decision.decision is PromotionDecision.REJECT_CHALLENGER
    assert decision.resulting_champion == experiment.champion


def test_eligible_evaluation_can_record_deliberate_promotion() -> None:
    service, engine = _setup()
    experiment = _experiment()
    evaluation = _evaluation(experiment)

    with engine.begin() as connection:
        service.register_experiment(connection, experiment)
        service.store_evaluation(connection, evaluation)
        decision = service.record_decision(
            connection,
            evaluation_id=evaluation.evaluation_id,
            decision=PromotionDecision.PROMOTE_CHALLENGER,
            rationale="All frozen promotion gates passed on independent confirmation.",
            created_at=DECIDED_AT,
        )

    assert decision.decision is PromotionDecision.PROMOTE_CHALLENGER
    assert decision.resulting_champion == experiment.champion


def test_decision_rejects_blank_rationale() -> None:
    service, engine = _setup()
    experiment = _experiment()
    evaluation = _evaluation(experiment)

    with engine.begin() as connection:
        service.register_experiment(connection, experiment)
        service.store_evaluation(connection, evaluation)
        with pytest.raises(service.ImprovementDecisionError, match="rationale"):
            service.record_decision(
                connection,
                evaluation_id=evaluation.evaluation_id,
                decision=PromotionDecision.KEEP_CHAMPION,
                rationale="   ",
                created_at=DECIDED_AT,
            )


def test_decision_rejects_timestamp_before_evaluation() -> None:
    service, engine = _setup()
    experiment = _experiment()
    evaluation = _evaluation(experiment)

    with engine.begin() as connection:
        service.register_experiment(connection, experiment)
        service.store_evaluation(connection, evaluation)
        with pytest.raises(service.ImprovementDecisionError, match="before evaluation"):
            service.record_decision(
                connection,
                evaluation_id=evaluation.evaluation_id,
                decision=PromotionDecision.KEEP_CHAMPION,
                rationale="Keep the accepted champion.",
                created_at=EVALUATED_AT - timedelta(seconds=1),
            )


def test_get_experiment_report_returns_append_only_history() -> None:
    service, engine = _setup()
    experiment = _experiment()
    evaluation = _evaluation(experiment)

    with engine.begin() as connection:
        service.register_experiment(connection, experiment)
        service.store_evaluation(connection, evaluation)
        decision = service.record_decision(
            connection,
            evaluation_id=evaluation.evaluation_id,
            decision=PromotionDecision.KEEP_CHAMPION,
            rationale="Keep the accepted champion while the challenger remains research-only.",
            created_at=DECIDED_AT,
        )
        report = service.get_experiment_report(connection, experiment.experiment_id)

    assert report["experiment"]["experiment_id"] == experiment.experiment_id
    assert [item["evaluation_id"] for item in report["evaluations"]] == [
        evaluation.evaluation_id
    ]
    assert [item["decision_id"] for item in report["decisions"]] == [decision.decision_id]
