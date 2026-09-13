from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IMPLEMENTATION_HEAD = "e09e9834260996553fa3cff7c18bf5269e48f4f0"
IMPLEMENTATION_CI_RUN = 34757241403
MERGED_MAIN_HEAD = "8b2d983ec75692de7ed39043c4a0e19dcf691942"
COVERAGE_SHA256 = "32c283a7769681ebe5b2e0d1fe255ad6c38aa5b0301303f8fe86f4e7b2278ffb"
EVIDENCE_PATH = "docs/evidence/phase-14-v3-gate-a-production-20260913.json"


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_project_state_records_production_accepted_v3_gate_a() -> None:
    state = json.loads(_text("PROJECT_STATE.json"))

    assert state["source_of_truth_version"] == "0.14.137"
    v3 = state["phase_14_btc_first_v3_gate_a"]
    assert v3["implementation_status"] == "PRODUCTION_COVERAGE_ACCEPTED_AWAITING_PREREGISTRATION"
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
    assert v3["coverage_start"] == "2026-09-13T13:45:00Z"
    assert v3["coverage_end"] == "2026-09-13T15:10:00Z"
    assert v3["target_markets"] == 17
    assert v3["feature_rows_before"] == 0
    assert v3["feature_rows_inserted"] == 68
    assert v3["feature_rows_after"] == 68
    assert v3["coverage_input_sha256"] == COVERAGE_SHA256
    assert v3["future_cutoff_violation_count"] == 0
    assert v3["polymarket_predictor_key_count"] == 0
    assert v3["current_state_missing_count"] == {
        "coinbase": 0,
        "bybit_spot": 0,
        "bybit_linear": 0,
    }
    assert v3["current_state_stale_count"] == {
        "coinbase": 0,
        "bybit_spot": 0,
        "bybit_linear": 0,
    }
    assert v3["expected_120s_missing_at_offset_60"] == 17
    assert v3["evidence"] == EVIDENCE_PATH
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
    assert "preregistration" in v3["next_action"].lower()
    assert "training" not in v3["next_action"].lower()


def test_v3_gate_a_production_evidence_is_sanitized_and_exact() -> None:
    evidence = json.loads(_text(EVIDENCE_PATH))

    assert evidence["verdict"] == "PASS"
    assert evidence["main_sha"] == MERGED_MAIN_HEAD
    assert evidence["feature_version"] == "core-v3-btc-native"
    assert evidence["coverage_start"] == "2026-09-13T13:45:00Z"
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


def test_canonical_docs_record_v3_gate_a_production_acceptance() -> None:
    start = _text("START-HERE.md")
    master = _text("docs/MASTER-SOURCE-OF-TRUTH.md")
    build = _text("docs/BUILD-ORDER.md")
    decisions = _text("docs/DECISION-LOG.md")
    changelog = _text("docs/CHANGELOG.md")

    for content in (start, master, build, decisions, changelog):
        assert "core-v3-btc-native" in content
        assert COVERAGE_SHA256 in content

    assert "PR #192" in master
    assert MERGED_MAIN_HEAD in master
    assert "17" in master
    assert "68" in master
    assert "Gate A" in master
    assert "PASS" in master

    assert "## D-046 — BTC-first V3 Gate A production coverage accepted" in decisions
    assert "V2 adaptive" in decisions
    assert "paused" in decisions.lower()

    assert "## 0.14.137 — 13 September 2026" in changelog
    entry = changelog.split("## 0.14.137 — 13 September 2026", 1)[1].split(
        "## 0.14.136", 1
    )[0]
    assert "17" in entry
    assert "68" in entry
    assert "outcome-blind" in entry.lower()
    assert "no training" in entry.lower()
    assert "preregistration" in entry.lower()


def test_immediate_next_action_is_preregistration_not_training() -> None:
    start = _text("START-HERE.md")
    build = _text("docs/BUILD-ORDER.md")

    start_next = start.split("## Immediate next task", 1)[1]
    build_next = build.split("## Immediate next action", 1)[1]
    for section in (start_next, build_next):
        assert "core-v3-btc-native" in section
        assert "preregistration" in section.lower()
        assert "training" not in section.lower()
        assert "adaptive-train" not in section
