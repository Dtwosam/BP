from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = (
    "docs/superpowers/specs/"
    "2026-09-14-phase-14-v3-gate-b-successor-preregistration.md"
)
PLAN_PATH = (
    "docs/superpowers/plans/"
    "2026-09-14-phase-14-v3-gate-b-successor-preregistration.md"
)
EVIDENCE_PATH = (
    "docs/evidence/phase-14-v3-gate-b-successor-preregistration-20260914.json"
)
SUCCESSOR_PLAN_VERSION = "v3-gate-b-preregister-v2"
SUCCESSOR_EPOCH_START = "2026-09-16T13:45:00Z"
SUCCESSOR_EPOCH_END = "2026-09-19T13:45:00Z"
RETIRED_PLAN_VERSION = "v3-gate-b-preregister-v1"


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_successor_preregistration_evidence_freezes_clean_future_epoch() -> None:
    evidence = json.loads(_text(EVIDENCE_PATH))

    assert evidence["status"] == "APPROVED_SUCCESSOR_PREREGISTRATION_DESIGN"
    retired = evidence["retired_attempt"]
    assert retired["research_plan_version"] == RETIRED_PLAN_VERSION
    assert retired["policy_selection_executable"] is False
    assert retired["diagnosis_identity_recoverable"] is False
    assert retired["epoch_data_policy"] == "engineering_coverage_only"

    successor = evidence["successor"]
    assert successor["research_plan_version"] == SUCCESSOR_PLAN_VERSION
    assert successor["dataset_version"] == "supervised-core-v3-btc-native-v1"
    assert successor["feature_version"] == "core-v3-btc-native"
    assert successor["label_version"] == "official-outcome-v1"
    assert successor["epoch_start"] == SUCCESSOR_EPOCH_START
    assert successor["epoch_end"] == SUCCESSOR_EPOCH_END
    assert successor["ordinary_fold_count"] == 5
    assert successor["final_holdout_hours"] == 12
    assert successor["eligibility"] == (
        "market_start_at >= epoch_start AND market_start_at < epoch_end"
    )
    assert successor["pre_epoch_markets_eligible"] is False
    assert successor["diagnosis_manifest_runtime_required"] is False
    assert successor["consumed_v2_manifest_runtime_required"] is False
    assert successor["historical_contamination_evidence_preserved"] is True
    assert successor["readiness_outcome_blind"] is True
    assert successor["planning_feature_only"] is True
    assert successor["final_holdout_separate_authorization_required"] is True

    safety = evidence["safety"]
    assert safety["training_authorized"] is False
    assert safety["final_holdout_access_authorized"] is False
    assert safety["model_activation_authorized"] is False
    assert safety["production_mutation_authorized"] is False
    assert safety["live_trading_enabled"] is False
    assert safety["max_trade_size_usd"] == 0
    assert safety["max_daily_loss_usd"] == 0


def test_successor_spec_and_plan_preserve_frozen_search_contract() -> None:
    spec = _text(SPEC_PATH)
    plan = _text(PLAN_PATH)

    for content in (spec, plan):
        assert RETIRED_PLAN_VERSION in content
        assert SUCCESSOR_PLAN_VERSION in content
        assert SUCCESSOR_EPOCH_START in content
        assert SUCCESSOR_EPOCH_END in content
        assert "core-v3-btc-native" in content
        assert "official-outcome-v1" in content
        assert "24h" in content
        assert "6h" in content
        assert "12h" in content
        assert "five ordinary folds" in content.lower()
        assert "final holdout" in content.lower()
        assert "outcome-blind" in content.lower()
        assert "feature-only" in content.lower()
        assert "pre-epoch" in content.lower()
        assert "diagnosis" in content.lower()
        assert "48" in content
        assert "no model fitting" in content.lower()
        assert "no final-holdout access" in content.lower()


def test_start_here_points_to_successor_implementation_before_epoch() -> None:
    start = _text("START-HERE.md")

    assert SPEC_PATH in start
    assert EVIDENCE_PATH in start
    assert RETIRED_PLAN_VERSION in start
    assert SUCCESSOR_PLAN_VERSION in start
    assert SUCCESSOR_EPOCH_START in start
    assert SUCCESSOR_EPOCH_END in start
    next_task = start.split("## Immediate next task", 1)[1].lower()
    assert "implement" in next_task
    assert "before" in next_task
    assert "2026-09-16t13:45:00z" in next_task
    assert "do not run" in next_task
    assert "readiness" in next_task
    assert "no model fitting" in next_task
