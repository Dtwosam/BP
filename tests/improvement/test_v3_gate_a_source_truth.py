from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IMPLEMENTATION_HEAD = "e09e9834260996553fa3cff7c18bf5269e48f4f0"
IMPLEMENTATION_CI_RUN = 34757241403


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_project_state_records_repository_only_v3_gate_a() -> None:
    state = json.loads(_text("PROJECT_STATE.json"))

    assert state["source_of_truth_version"] == "0.14.136"
    v3 = state["phase_14_btc_first_v3_gate_a"]
    assert v3["implementation_status"] == "REPOSITORY_IMPLEMENTED_AWAITING_COVERAGE_ACCEPTANCE"
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
    assert v3["v2_adaptive_training_paused"] is True
    assert v3["training_performed"] is False
    assert v3["production_rollout_performed"] is False
    assert v3["production_migration_performed"] is False
    assert v3["model_activation_performed"] is False
    assert v3["paper_activation_performed"] is False
    assert v3["final_holdout_access_performed"] is False
    assert v3["automatic_promotion"] is False
    assert v3["live_trading_enabled"] is False
    assert v3["max_trade_size_usd"] == 0
    assert v3["max_daily_loss_usd"] == 0
    assert "Gate A" in v3["next_action"]
    assert "coverage" in v3["next_action"].lower()


def test_canonical_docs_record_v3_gate_a_without_overclaiming_acceptance() -> None:
    start = _text("START-HERE.md")
    master = _text("docs/MASTER-SOURCE-OF-TRUTH.md")
    build = _text("docs/BUILD-ORDER.md")
    decisions = _text("docs/DECISION-LOG.md")
    changelog = _text("docs/CHANGELOG.md")

    for content in (start, master, build, decisions, changelog):
        assert "core-v3-btc-native" in content

    assert "PR #192" in master
    assert IMPLEMENTATION_HEAD in master
    assert str(IMPLEMENTATION_CI_RUN) in master
    assert "production rollout" in master.lower()
    assert "not" in master.lower()

    assert "## D-045 — BTC-first V3 Gate A" in decisions
    assert "V2 adaptive" in decisions
    assert "paused" in decisions.lower()

    assert "## 0.14.136 — 13 September 2026" in changelog
    entry = changelog.split("## 0.14.136 — 13 September 2026", 1)[1].split(
        "## 0.14.135", 1
    )[0]
    assert "PR #192" in entry
    assert "outcome-blind" in entry.lower()
    assert "no production" in entry.lower()
    assert "no training" in entry.lower()


def test_immediate_next_action_is_coverage_and_gate_a_not_training_or_gate_b() -> None:
    start = _text("START-HERE.md")
    build = _text("docs/BUILD-ORDER.md")

    start_next = start.split("## Immediate next task", 1)[1]
    build_next = build.split("## Immediate next action", 1)[1]
    for section in (start_next, build_next):
        assert "core-v3-btc-native" in section
        assert "coverage" in section.lower()
        assert "Gate A" in section
        assert "adaptive-train" not in section
        assert "Gate B" not in section
