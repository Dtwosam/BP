from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bp_engine.improvement.evidence import EvidenceIntegrityError, validate_evidence_manifest
from bp_engine.improvement.models import (
    EXPERIMENT_VERSION,
    ChampionRef,
    ChangeFamily,
    EvidenceItem,
    EvidenceRole,
    ImprovementExperimentSpec,
)

FREEZE_AT = datetime(2026, 8, 29, 8, 0, tzinfo=UTC)


def _champion() -> ChampionRef:
    return ChampionRef(
        calibration_run_id="phase9-300-c9f0e00eb7836af08008c66909f8f179",
        calibration_semantic_sha256="a" * 64,
        backtest_run_id="phase8-300-example",
        backtest_semantic_sha256="b" * 64,
        training_run_id="phase7-300-example",
        training_semantic_sha256="c" * 64,
    )


def _experiment(
    *,
    selection_roles: tuple[str, ...] = ("development_validation",),
) -> ImprovementExperimentSpec:
    return ImprovementExperimentSpec.build(
        experiment_version=EXPERIMENT_VERSION,
        hypothesis="A spread guard improves executable economics.",
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
        selection_policy={"allowed_roles": list(selection_roles)},
        confirmation_policy={"allowed_roles": ["fresh_holdout", "prospective_paper"]},
        cost_assumptions={"fee_rate": 0.07, "slippage_buffer": 0.01},
        primary_metric="net_pnl_delta",
        guardrail_metrics=("calibrated_log_loss", "calibrated_brier"),
        legacy_confirmation_identifiers=("legacy-final-1",),
        created_at=FREEZE_AT,
    )


def _evidence(
    *,
    identifier: str,
    role: EvidenceRole,
    observed_at: datetime | None = None,
    prediction_id: str | None = None,
) -> EvidenceItem:
    return EvidenceItem(
        identifier=identifier,
        role=role,
        condition_id=identifier,
        prediction_id=prediction_id,
        observed_at=observed_at or FREEZE_AT + timedelta(days=1),
        resolved=True,
        net_pnl=0.10,
    )


def test_valid_manifest_accepts_development_and_independent_confirmation() -> None:
    experiment = _experiment()
    evidence = (
        _evidence(identifier="development-1", role=EvidenceRole.DEVELOPMENT_VALIDATION),
        _evidence(identifier="ordinary-1", role=EvidenceRole.ORDINARY_OOS),
        _evidence(identifier="fresh-1", role=EvidenceRole.FRESH_HOLDOUT),
        _evidence(
            identifier="paper-1",
            role=EvidenceRole.PROSPECTIVE_PAPER,
            prediction_id="prediction-1",
        ),
    )

    validate_evidence_manifest(
        experiment=experiment,
        evidence=evidence,
        prior_confirmation_identifiers=set(),
    )


def test_known_legacy_final_holdout_cannot_be_relabelled_fresh() -> None:
    experiment = _experiment()
    legacy_final_as_fresh_holdout = _evidence(
        identifier="legacy-final-1",
        role=EvidenceRole.FRESH_HOLDOUT,
    )

    with pytest.raises(EvidenceIntegrityError, match="known legacy final holdout"):
        validate_evidence_manifest(
            experiment=experiment,
            evidence=(legacy_final_as_fresh_holdout,),
            prior_confirmation_identifiers=set(),
        )


def test_prospective_paper_must_postdate_challenger_freeze() -> None:
    experiment = _experiment()
    prospective_prediction_created_before_experiment = _evidence(
        identifier="paper-before-freeze",
        role=EvidenceRole.PROSPECTIVE_PAPER,
        prediction_id="prediction-before-freeze",
        observed_at=FREEZE_AT,
    )

    with pytest.raises(EvidenceIntegrityError, match="must post-date challenger freeze"):
        validate_evidence_manifest(
            experiment=experiment,
            evidence=(prospective_prediction_created_before_experiment,),
            prior_confirmation_identifiers=set(),
        )


def test_fresh_confirmation_identifier_cannot_be_reused() -> None:
    experiment = _experiment()
    fresh_holdout_item = _evidence(
        identifier="fresh-consumed",
        role=EvidenceRole.FRESH_HOLDOUT,
    )

    with pytest.raises(EvidenceIntegrityError, match="already consumed"):
        validate_evidence_manifest(
            experiment=experiment,
            evidence=(fresh_holdout_item,),
            prior_confirmation_identifiers={fresh_holdout_item.identifier},
        )


def test_selection_policy_cannot_include_confirmation_roles() -> None:
    experiment = _experiment(
        selection_roles=("development_validation", "fresh_holdout")
    )

    with pytest.raises(EvidenceIntegrityError, match="selection policy"):
        validate_evidence_manifest(
            experiment=experiment,
            evidence=(),
            prior_confirmation_identifiers=set(),
        )


def test_prospective_paper_requires_prediction_identity() -> None:
    experiment = _experiment()
    item = _evidence(
        identifier="paper-without-prediction",
        role=EvidenceRole.PROSPECTIVE_PAPER,
    )

    with pytest.raises(EvidenceIntegrityError, match="prediction_id"):
        validate_evidence_manifest(
            experiment=experiment,
            evidence=(item,),
            prior_confirmation_identifiers=set(),
        )
