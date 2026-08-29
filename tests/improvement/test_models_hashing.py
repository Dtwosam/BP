from __future__ import annotations

from datetime import UTC, datetime

import pytest


def _api():
    from bp_engine.improvement.hashing import derive_seed, semantic_sha256
    from bp_engine.improvement.models import (
        EXPERIMENT_VERSION,
        ChampionRef,
        ChangeFamily,
        EvidenceRole,
        ImprovementExperimentSpec,
        PromotionDecision,
    )

    return {
        "derive_seed": derive_seed,
        "semantic_sha256": semantic_sha256,
        "EXPERIMENT_VERSION": EXPERIMENT_VERSION,
        "ChampionRef": ChampionRef,
        "ChangeFamily": ChangeFamily,
        "EvidenceRole": EvidenceRole,
        "ImprovementExperimentSpec": ImprovementExperimentSpec,
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


def test_phase13_api_exists_and_enums_are_frozen():
    api = _api()

    assert api["EXPERIMENT_VERSION"] == "improvement-experiment-v1"
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
