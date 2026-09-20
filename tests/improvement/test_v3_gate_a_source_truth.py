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
PREREGISTRATION_IMPLEMENTATION_HEAD = "39a887e138398215ac97dc45f8099e3515a90fe2"
PREREGISTRATION_CI_RUN = 34775202056
EPOCH_START = "2026-09-13T13:45:00Z"
EPOCH_END = "2026-09-16T13:45:00Z"
SUCCESSOR_PLAN_VERSION = "v3-gate-b-preregister-v2"
SUCCESSOR_EPOCH_START = "2026-09-16T13:45:00Z"
SUCCESSOR_EPOCH_END = "2026-09-19T13:45:00Z"


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_project_state_records_gate_a_pass_and_frozen_v3_preregistration() -> None:
    state = json.loads(_text("PROJECT_STATE.json"))

    assert state["source_of_truth_version"] == "0.14.145"
    v3 = state["phase_14_btc_first_v3_gate_a"]
    assert (
        v3["implementation_status"]
        == "GATE_A_PASS_V3_GATE_B_FINAL_HOLDOUT_CONSUMED_V4_REGIME_RESEARCH_BUILD"
    )
    assert v3["feature_version"] == "core-v3-btc-native"
    assert v3["label_version"] == "official-outcome-v1"
    assert v3["horizon_seconds"] == 300
    assert v3["offsets_seconds"] == [60, 120, 180, 240]
    assert v3["forecast_sources"] == ["coinbase_spot", "bybit_spot", "bybit_linear"]
    assert v3["btc_only_forecast_contract"] is True
    assert v3["polymarket_predictor_keys_allowed"] is False
    assert v3["outcome_blind_coverage_report"] is True
    assert v3["future_data_perturbation_test"] is True
    assert v3["pr"] == 192
    assert v3["implementation_head"] == IMPLEMENTATION_HEAD
    assert v3["implementation_ci_run_id"] == IMPLEMENTATION_CI_RUN
    assert v3["implementation_ci_passed"] is True
    assert v3["merged_main_head"] == MERGED_MAIN_HEAD
    assert v3["production_materialization_performed"] is True
    assert v3["production_materialization_passed"] is True
    assert v3["coverage_acceptance"] == "PASS"
    assert v3["coverage_start"] == EPOCH_START
    assert v3["coverage_end"] == "2026-09-13T15:10:00Z"
    assert v3["target_markets"] == 17
    assert v3["feature_rows_before"] == 0
    assert v3["feature_rows_inserted"] == 68
    assert v3["feature_rows_after"] == 68
    assert v3["coverage_input_sha256"] == COVERAGE_SHA256
    assert v3["future_cutoff_violation_count"] == 0
    assert v3["polymarket_predictor_key_count"] == 0
    assert v3["evidence"] == EVIDENCE_PATH
    assert v3["preregistration_frozen"] is True
    assert v3["preregistration_design_commit"] == PREREGISTRATION_DESIGN_COMMIT
    assert v3["preregistration_issue"] == 193
    assert v3["preregistration_implementation_head"] == PREREGISTRATION_IMPLEMENTATION_HEAD
    assert v3["preregistration_ci_run_id"] == PREREGISTRATION_CI_RUN
    assert v3["preregistration_ci_passed"] is True
    assert v3["research_plan_version"] == "v3-gate-b-preregister-v1"
    assert v3["prospective_epoch_start"] == EPOCH_START
    assert v3["prospective_epoch_end"] == EPOCH_END
    assert v3["ordinary_fold_count"] == 5
    assert v3["final_holdout_hours"] == 12
    assert v3["v2_adaptive_training_paused"] is True
    assert v3["training_performed"] is False
    assert v3["production_migration_performed"] is False
    assert v3["model_activation_performed"] is False
    assert v3["paper_activation_performed"] is False
    assert v3["final_holdout_access_performed"] is False
    assert v3["automatic_promotion"] is False
    assert v3["live_trading_enabled"] is False
    assert v3["max_trade_size_usd"] == 0
    assert v3["max_daily_loss_usd"] == 0
    next_action = v3["next_action"].lower()
    assert "v3 gate b successor is complete" in next_action
    assert "permanently consumed" in next_action
    assert "v4 regime-aware challenger" in next_action
    assert "do not tune v4" in next_action


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


def test_canonical_docs_record_gate_a_pass_and_preregistration_handoff() -> None:
    start = _text("START-HERE.md")
    master = _text("docs/MASTER-SOURCE-OF-TRUTH.md")
    build = _text("docs/BUILD-ORDER.md")
    decisions = _text("docs/DECISION-LOG.md")
    changelog = _text("docs/CHANGELOG.md")

    for content in (start, master, decisions, changelog):
        assert "core-v3-btc-native" in content
        assert COVERAGE_SHA256 in content
        assert PREREGISTRATION_DESIGN_COMMIT in content
        assert EPOCH_END in content

    assert "frozen V3" in build
    assert "paper" in build.lower()
    assert "v4 regime-aware feature collection" in build.lower()

    current_master = master.split(
        "## Phase 14 BTC-first V3 Gate A production acceptance + Gate B preregistration",
        1,
    )[1]
    assert "Gate A" in current_master
    assert "PASS" in current_master
    assert "Issue #193" in current_master
    assert PREREGISTRATION_IMPLEMENTATION_HEAD in current_master
    assert str(PREREGISTRATION_CI_RUN) in current_master

    assert "## D-046 — BTC-first V3 Gate A production coverage accepted" in decisions
    assert "## D-047 — V3 Gate B preregistration frozen" in decisions

    assert "## 0.14.138 — 13 September 2026" in changelog
    assert "## 0.14.137 — 13 September 2026" in changelog
    entry = changelog.split("## 0.14.138 — 13 September 2026", 1)[1].split(
        "## 0.14.137", 1
    )[0]
    assert "preregistration" in entry.lower()
    assert "outcome-blind" in entry.lower()
    assert "no training" in entry.lower()
    assert PREREGISTRATION_IMPLEMENTATION_HEAD in entry


def test_current_handoff_points_to_active_v3_paper_observation() -> None:
    start = _text("START-HERE.md")
    build = _text("docs/BUILD-ORDER.md")

    start_next = start.split("## Immediate next task", 1)[1].lower()
    assert "production pass and active" in start_next
    assert "v3-frozen-paper-v1" in start_next
    assert "paper-execution-v3-frozen-v1" in start_next
    assert "real_money" in start_next and "$0.00" in start_next
    assert "prospective observation only" in start_next

    build_next = build.split("## Immediate next action", 1)[1].lower()
    assert "production pass and active" in build_next
    assert "0.075" in build_next
    assert "real money at zero" in build_next
    assert "do not tune v3 from paper results" in build_next
