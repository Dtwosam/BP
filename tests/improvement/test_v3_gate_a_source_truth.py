from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IMPLEMENTATION_HEAD = "e09e9834260996553fa3cff7c18bf5269e48f4f0"
IMPLEMENTATION_CI_RUN = 34757241403
MERGED_MAIN_HEAD = "8b2d983ec75692de7ed39043c4a0e19dcf691942"
COVERAGE_SHA256 = "32c283a7769681ebe5b2e0d1fe255ad6c38aa5b0301303f8fe86f4e7b2278ffb"
EVIDENCE_PATH = "docs/evidence/phase-14-v3-gate-a-production-20260913.json"
HANDOFF_PATH = "docs/evidence/phase-14-v3-preregistration-handoff-20260913.json"
PREREGISTRATION_DESIGN_COMMIT = "c9e179c91ea990ca4a25a13f69fc5932811fb32a"
PREREGISTRATION_IMPLEMENTATION_HEAD = "4efa403f0fcfcc9a7d4717c48d6da721fb3a1f38"
PREREGISTRATION_CI_RUN = 34773596855
EPOCH_START = "2026-09-13T13:45:00Z"
EPOCH_END = "2026-09-16T13:45:00Z"


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v3_gate_a_production_evidence_is_sanitized_and_exact() -> None:
    evidence = json.loads(_text(EVIDENCE_PATH))

    assert evidence["verdict"] == "PASS"
    assert evidence["main_sha"] == MERGED_MAIN_HEAD
    assert evidence["feature_version"] == "core-v3-btc-native"
    assert evidence["coverage_start"] == EPOCH_START
    assert evidence["coverage_end"] == "2026-09-13T15:10:00Z"
    assert evidence["target_markets"] == 17
    assert evidence["v3_rows_before"] == 0
    assert evidence["generation"]["planned_rows"] == 68
    assert evidence["generation"]["inserted"] == 68
    assert evidence["generation"]["existing"] == 0
    assert evidence["v3_rows_after"] == 68
    coverage = evidence["coverage"]
    assert coverage["row_count"] == 68
    assert coverage["market_count"] == 17
    assert coverage["offsets"] == [60, 120, 180, 240]
    assert coverage["coverage_input_sha256"] == COVERAGE_SHA256
    assert coverage["future_cutoff_violation_count"] == 0
    assert coverage["polymarket_predictor_key_count"] == 0
    assert coverage["policy_selected"] is False
    assert coverage["training_run"] is False
    assert coverage["automatic_promotion"] is False
    for source in ("coinbase", "bybit_spot", "bybit_linear"):
        current = coverage["sources"][source]["current_state"]
        assert current["available_count"] == 68
        assert current["missing_count"] == 0
        assert current["stale_count"] == 0
    assert evidence["training_performed"] is False
    assert evidence["model_activated"] is False
    assert evidence["live_trading_changed"] is False
    serialized = json.dumps(evidence, sort_keys=True).lower()
    for forbidden in ("private_key", "wallet_address", "password", "secret"):
        assert forbidden not in serialized


def test_v3_preregistration_handoff_freezes_gate_a_and_gate_b_boundaries() -> None:
    handoff = json.loads(_text(HANDOFF_PATH))

    assert handoff["status"] == "GATE_A_PASS_V3_GATE_B_PREREGISTRATION_FROZEN"
    assert handoff["feature_version"] == "core-v3-btc-native"
    assert handoff["label_version"] == "official-outcome-v1"

    gate_a = handoff["gate_a"]
    assert gate_a["implementation_head"] == IMPLEMENTATION_HEAD
    assert gate_a["implementation_ci_run_id"] == IMPLEMENTATION_CI_RUN
    assert gate_a["merged_main_head"] == MERGED_MAIN_HEAD
    assert gate_a["production_coverage_verdict"] == "PASS"
    assert gate_a["production_evidence"] == EVIDENCE_PATH
    assert gate_a["coverage_input_sha256"] == COVERAGE_SHA256
    assert gate_a["market_count"] == 17
    assert gate_a["row_count"] == 68

    prereg = handoff["preregistration"]
    assert prereg["design_commit"] == PREREGISTRATION_DESIGN_COMMIT
    assert prereg["issue"] == 193
    assert prereg["implementation_head"] == PREREGISTRATION_IMPLEMENTATION_HEAD
    assert prereg["implementation_ci_run_id"] == PREREGISTRATION_CI_RUN
    assert prereg["implementation_ci_passed"] is True
    assert prereg["research_plan_version"] == "v3-gate-b-preregister-v1"
    assert prereg["epoch_start"] == EPOCH_START
    assert prereg["epoch_end"] == EPOCH_END
    assert prereg["ordinary_fold_count"] == 5
    assert prereg["final_holdout_hours"] == 12
    assert prereg["exclusion_kinds"] == ["diagnosis", "consumed_v2_final_holdout"]
    assert prereg["readiness_outcome_blind"] is True
    assert prereg["planning_feature_only"] is True
    assert prereg["readiness_artifact_free"] is True
    assert prereg["planning_database_writes"] is False
    assert prereg["final_holdout_separate_authorization_required"] is True

    safety = handoff["safety"]
    assert safety["v2_adaptive_training_paused"] is True
    assert safety["training_performed"] is False
    assert safety["final_holdout_access_performed"] is False
    assert safety["model_activation_performed"] is False
    assert safety["paper_activation_performed"] is False
    assert safety["automatic_promotion"] is False
    assert safety["live_trading_enabled"] is False
    assert safety["max_trade_size_usd"] == 0
    assert safety["max_daily_loss_usd"] == 0

    next_action = handoff["next_action"].lower()
    assert EPOCH_END.lower() in next_action
    assert "outcome-blind" in next_action
    assert "readiness" in next_action
    assert "feature-only" in next_action
    assert "five-fold" in next_action
    assert "do not train" in next_action
    assert "do not" in next_action and "final holdout" in next_action


def test_start_here_records_gate_a_pass_and_frozen_preregistration() -> None:
    start = _text("START-HERE.md")

    assert "core-v3-btc-native" in start
    assert EVIDENCE_PATH in start
    assert HANDOFF_PATH in start
    assert COVERAGE_SHA256 in start
    assert PREREGISTRATION_DESIGN_COMMIT in start
    assert "Issue #193" in start
    assert PREREGISTRATION_IMPLEMENTATION_HEAD in start
    assert str(PREREGISTRATION_CI_RUN) in start
    assert "Gate A production coverage is now accepted **PASS**" in start
    assert EPOCH_END in start


def test_immediate_next_action_is_epoch_then_readiness_then_feature_only_plan() -> None:
    start = _text("START-HERE.md")
    section = start.split("## Immediate next task", 1)[1]
    lowered = section.lower()

    assert "core-v3-btc-native" in section
    assert EPOCH_END in section
    assert "outcome-blind" in lowered
    assert "readiness" in lowered
    assert "feature-only" in lowered
    assert "plan" in lowered
    assert "adaptive-train" not in lowered
    assert "do not start model fitting" in lowered
    assert "do not read final-holdout labels" in lowered
    assert "do not" in lowered and "evaluate the final holdout" in lowered
