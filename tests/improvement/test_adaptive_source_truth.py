from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_master_records_adaptive_learning_and_consumed_gate_b() -> None:
    master = _text("docs/MASTER-SOURCE-OF-TRUTH.md")

    assert "### Phase 14 adaptive learning research contract — 12 September 2026" in master
    section = master.split(
        "### Phase 14 adaptive learning research contract — 12 September 2026", 1
    )[1]
    assert "50 newly resolved eligible markets" in section
    assert "executed trades are not required" in section.lower()
    assert "automatic_promotion=false" in section
    assert "final holdout" in section.lower()
    assert "permanently consumed" in section.lower()
    assert "no_validation_edge_candidate_profitable" in section


def test_project_state_keeps_live_gate_blocked_and_records_research_cycle() -> None:
    state = json.loads(_text("PROJECT_STATE.json"))

    assert state["source_of_truth_version"] == "0.14.133"
    assert state["current_phase"] == 14
    assert state["status"] == "PHASE_14_ENGINEERING_COMPLETE_LIVE_GATE_BLOCKED"
    assert state["trading_mode"] == "RESEARCH"
    assert state["live_trading_enabled"] is False

    adaptive = state["phase_14_adaptive_learning"]
    assert adaptive["implementation_status"] == "REPOSITORY_RESEARCH_ONLY"
    assert adaptive["trigger_basis"] == "newly_resolved_eligible_markets"
    assert adaptive["initial_trigger_count"] == 50
    assert adaptive["executed_trade_required"] is False
    assert adaptive["automatic_promotion"] is False
    assert adaptive["final_holdout_access_allowed"] is False
    assert adaptive["paper_model_activation_allowed"] is False
    assert adaptive["live_trading_enabled"] is False
    assert adaptive["max_trade_size_usd"] == 0
    assert adaptive["max_daily_loss_usd"] == 0

    gate_b = state["phase_14_market_price_v2_followup"]
    assert gate_b["gate_b_latest_evidence_status"] == "COMPLETE_NOT_ACCEPTED"
    assert gate_b["gate_b_latest_final_policy"] == "no_trade"
    assert gate_b["gate_b_latest_final_selection_reason"] == (
        "no_validation_edge_candidate_profitable"
    )
    assert gate_b["gate_b_latest_holdout_reusable"] is False
    assert gate_b["gate_b_latest_v2_paper_activation_authorized"] is False


def test_build_order_and_decision_log_point_to_adaptive_research() -> None:
    build_order = _text("docs/BUILD-ORDER.md")
    decisions = _text("docs/DECISION-LOG.md")

    assert "## Phase 14 adaptive learning research milestone — 12 September 2026" in build_order
    immediate = build_order.split("## Immediate next action", 1)[1]
    assert "adaptive-readiness" in immediate
    assert "50 newly resolved eligible markets" in immediate
    assert "fresh final holdout" not in immediate.lower()

    assert "## D-042 — Resolved markets drive adaptive supervised learning" in decisions
    d042 = decisions.split("## D-042", 1)[1]
    assert "**Status:** Active" in d042
    assert "50 newly resolved eligible markets" in d042
    assert "automatic_promotion=false" in d042
    assert "no_validation_edge_candidate_profitable" in d042


def test_changelog_and_design_mark_research_only_implementation() -> None:
    changelog = _text("docs/CHANGELOG.md")
    design = _text("docs/superpowers/specs/2026-09-12-adaptive-bitcoin-learning-design.md")

    assert "## 0.14.133 — 12 September 2026" in changelog
    entry = changelog.split("## 0.14.133 — 12 September 2026", 1)[1].split(
        "## 0.14.132", 1
    )[0]
    assert "50 newly resolved eligible markets" in entry
    assert "research-only" in entry.lower()
    assert "no_trade" in entry
    assert "automatic promotion" in entry.lower()

    assert (
        "**Status:** Implemented research-only milestone; no production mutation "
        "and no model activation"
    ) in design

def test_first_adaptive_cycle_bootstrap_boundary_is_frozen() -> None:
    state = json.loads(_text("PROJECT_STATE.json"))
    master = _text("docs/MASTER-SOURCE-OF-TRUTH.md")
    build_order = _text("docs/BUILD-ORDER.md")
    decisions = _text("docs/DECISION-LOG.md")
    changelog = _text("docs/CHANGELOG.md")

    adaptive = state["phase_14_adaptive_learning"]
    assert adaptive["first_cycle_bootstrap_since_at"] == "2026-09-12T17:21:13Z"
    assert state["source_of_truth_version"] == "0.14.134"
    assert "2026-09-12T17:21:13Z" in master
    assert "2026-09-12T17:21:13Z" in build_order
    assert "## D-043 — Freeze first adaptive-cycle bootstrap boundary" in decisions
    assert "2026-09-12T17:21:13Z" in decisions.split("## D-043", 1)[1]
    assert "## 0.14.134 — 12 September 2026" in changelog
