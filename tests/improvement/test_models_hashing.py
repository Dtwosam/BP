from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest


def _api():
    from bp_engine.improvement.hashing import derive_seed, semantic_sha256
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

    return {
        "derive_seed": derive_seed,
        "semantic_sha256": semantic_sha256,
        "DECISION_VERSION": DECISION_VERSION,
        "EVALUATION_VERSION": EVALUATION_VERSION,
        "EXPERIMENT_VERSION": EXPERIMENT_VERSION,
        "ChampionRef": ChampionRef,
        "ChangeFamily": ChangeFamily,
        "EvidenceItem": EvidenceItem,
        "EvidenceRole": EvidenceRole,
        "ImprovementEvaluationReport": ImprovementEvaluationReport,
        "ImprovementExperimentSpec": ImprovementExperimentSpec,
        "ImprovementPromotionDecision": ImprovementPromotionDecision,
        "PolicyMetrics": PolicyMetrics,
        "PromotionDecision": PromotionDecision,
    }


def _champion(api, **overrides):
    values = {
        "calibration_run_id": "phase9-300-c9f0e00eb7836af08008c66909f8f179",
        "calibration_semantic_sha256": "a" * 64,
        "backtest_run_id": "phase8-300-example",
        "backtest_semantic_sha256": "b" * 64,
        "training_run_id": "phase7-300-example",
        "training_semantic_sha256": "c" * 64,
    }
    values.update(overrides)
    return api["ChampionRef"](**values)


def _spec(api, **overrides):
    values = {
        "experiment_version": api["EXPERIMENT_VERSION"],
        "hypothesis": "A max-spread guard reduces negative executable outcomes.",
        "horizon_seconds": 300,
        "change_family": api["ChangeFamily"].ABSTENTION,
        "champion": _champion(api),
        "challenger": {"max_spread_grid": [0.02, 0.04, 0.06, None]},
        "source_versions": {
            "dataset": "supervised-core-v1",
            "feature": "core-v1",
            "label": "official-outcome-v1",
        },
        "research_start": datetime(2026, 8, 24, tzinfo=UTC),
        "research_end": datetime(2026, 8, 25, tzinfo=UTC),
        "selection_policy": {"allowed_roles": ["development_validation"]},
        "confirmation_policy": {
            "allowed_roles": ["fresh_holdout", "prospective_paper"]
        },
        "cost_assumptions": {"fee_rate": 0.07, "slippage_buffer": 0.01},
        "primary_metric": "net_pnl_delta",
        "guardrail_metrics": ("calibrated_log_loss", "calibrated_brier"),
        "legacy_confirmation_identifiers": ("legacy-holdout-1",),
        "created_at": datetime(2026, 8, 29, tzinfo=UTC),
    }
    values.update(overrides)
    return api["ImprovementExperimentSpec"].build(**values)


def _metrics(api, **overrides):
    values = {
        "calibrated_log_loss": 0.4,
        "calibrated_brier": 0.15,
        "ece": 0.03,
        "accuracy": 0.8,
        "trade_count": 4,
        "trade_coverage": 0.4,
        "total_net_pnl": 0.25,
        "mean_net_pnl_per_trade": 0.0625,
        "max_drawdown": 0.1,
        "max_losing_streak": 1,
        "unresolved_count": 0,
        "missing_unexecutable_count": 0,
    }
    values.update(overrides)
    return api["PolicyMetrics"](**values)


def _evaluation(api, **overrides):
    spec = _spec(api)
    evidence = (
        api["EvidenceItem"](
            identifier="fresh-condition-1",
            role=api["EvidenceRole"].FRESH_HOLDOUT,
            condition_id="fresh-condition-1",
            prediction_id=None,
            observed_at=datetime(2026, 8, 30, tzinfo=UTC),
            resolved=True,
            net_pnl=0.1,
        ),
    )
    values = {
        "evaluation_version": api["EVALUATION_VERSION"],
        "experiment_id": spec.experiment_id,
        "challenger_id": "spread-guard-v1",
        "challenger_semantic_sha256": "d" * 64,
        "challenger_config": {"max_spread": 0.04},
        "evidence_manifest": evidence,
        "champion_metrics": _metrics(api, total_net_pnl=0.05),
        "challenger_metrics": _metrics(api, total_net_pnl=0.15),
        "comparison": {"mean_delta": 0.1, "lower_95": 0.01, "upper_95": 0.2},
        "promotion_eligible": True,
        "ineligibility_reasons": (),
        "created_at": datetime(2026, 8, 31, tzinfo=UTC),
    }
    values.update(overrides)
    return api["ImprovementEvaluationReport"].build(**values)


def test_phase13_api_exists_and_enums_are_frozen():
    api = _api()

    assert api["EXPERIMENT_VERSION"] == "improvement-experiment-v1"
    assert api["EVALUATION_VERSION"] == "improvement-evaluation-v1"
    assert api["DECISION_VERSION"] == "improvement-decision-v1"
    assert api["ChangeFamily"].ABSTENTION.value == "abstention"
    assert api["EvidenceRole"].FRESH_HOLDOUT.value == "fresh_holdout"
    assert api["PromotionDecision"].KEEP_CHAMPION.value == "keep_champion"


def test_semantic_hash_is_independent_of_mapping_order():
    api = _api()

    left = {"b": 2, "a": {"d": 4, "c": 3}}
    right = {"a": {"c": 3, "d": 4}, "b": 2}

    assert api["semantic_sha256"](left) == api["semantic_sha256"](right)


def test_experiment_semantics_ignore_created_at():
    api = _api()

    first = _spec(api, created_at=datetime(2026, 8, 29, tzinfo=UTC))
    second = _spec(api, created_at=datetime(2026, 8, 30, tzinfo=UTC))

    assert first.experiment_id == second.experiment_id
    assert first.semantic_sha256 == second.semantic_sha256
    assert first.experiment_id == f"phase13-exp-{first.semantic_sha256[:32]}"


def test_experiment_semantics_change_when_hypothesis_changes():
    api = _api()

    first = _spec(api)
    second = _spec(
        api,
        hypothesis="A different falsifiable hypothesis with different semantics.",
    )

    assert first.experiment_id != second.experiment_id
    assert first.semantic_sha256 != second.semantic_sha256


def test_experiment_rejects_blank_hypothesis_and_naive_timestamps():
    api = _api()

    with pytest.raises(ValueError, match="hypothesis"):
        _spec(api, hypothesis="   ")

    with pytest.raises(ValueError, match="research_start"):
        _spec(api, research_start=datetime(2026, 8, 24))

    with pytest.raises(ValueError, match="created_at"):
        _spec(api, created_at=datetime(2026, 8, 29))


def test_champion_rejects_malformed_semantic_hash():
    api = _api()

    with pytest.raises(ValueError, match="calibration_semantic_sha256"):
        _champion(api, calibration_semantic_sha256="bad")


def test_derive_seed_is_stable_and_order_sensitive():
    api = _api()

    first = api["derive_seed"]("experiment", "champion", "evidence")
    second = api["derive_seed"]("experiment", "champion", "evidence")
    reversed_parts = api["derive_seed"]("evidence", "champion", "experiment")

    assert first == second
    assert isinstance(first, int)
    assert first >= 0
    assert first != reversed_parts


def test_evaluation_semantics_ignore_created_at_and_sort_reasons():
    api = _api()

    first = _evaluation(
        api,
        ineligibility_reasons=("z_reason", "a_reason", "z_reason"),
        promotion_eligible=False,
        created_at=datetime(2026, 8, 31, tzinfo=UTC),
    )
    second = _evaluation(
        api,
        ineligibility_reasons=("a_reason", "z_reason"),
        promotion_eligible=False,
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
    )

    assert first.evaluation_id == second.evaluation_id
    assert first.semantic_sha256 == second.semantic_sha256
    assert first.ineligibility_reasons == ("a_reason", "z_reason")
    assert first.evaluation_id == f"phase13-eval-{first.semantic_sha256[:32]}"


def test_policy_metrics_reject_nonfinite_or_invalid_values():
    api = _api()

    with pytest.raises(ValueError, match="calibrated_log_loss"):
        _metrics(api, calibrated_log_loss=float("nan"))
    with pytest.raises(ValueError, match="trade_coverage"):
        _metrics(api, trade_coverage=1.1)
    with pytest.raises(ValueError, match="trade_count"):
        _metrics(api, trade_count=-1)


def test_decision_semantics_ignore_created_at_and_require_rationale():
    api = _api()
    evaluation = _evaluation(api)
    champion = _champion(api)

    first = api["ImprovementPromotionDecision"].build(
        decision_version=api["DECISION_VERSION"],
        evaluation_id=evaluation.evaluation_id,
        experiment_id=evaluation.experiment_id,
        decision=api["PromotionDecision"].KEEP_CHAMPION,
        rationale="Independent confirmation is not sufficient for promotion.",
        resulting_champion=champion,
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    second = api["ImprovementPromotionDecision"].build(
        decision_version=api["DECISION_VERSION"],
        evaluation_id=evaluation.evaluation_id,
        experiment_id=evaluation.experiment_id,
        decision=api["PromotionDecision"].KEEP_CHAMPION,
        rationale="Independent confirmation is not sufficient for promotion.",
        resulting_champion=champion,
        created_at=datetime(2026, 9, 2, tzinfo=UTC),
    )

    assert first.decision_id == second.decision_id
    assert first.semantic_sha256 == second.semantic_sha256
    assert first.decision_id == f"phase13-decision-{first.semantic_sha256[:32]}"

    with pytest.raises(ValueError, match="rationale"):
        replace(first, rationale="").__class__.build(
            decision_version=api["DECISION_VERSION"],
            evaluation_id=evaluation.evaluation_id,
            experiment_id=evaluation.experiment_id,
            decision=api["PromotionDecision"].KEEP_CHAMPION,
            rationale="   ",
            resulting_champion=champion,
            created_at=datetime(2026, 9, 1, tzinfo=UTC),
        )
