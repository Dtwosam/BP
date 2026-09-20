from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODEL_SHA = "124627e15cab3997b8abe54ec5237450d976ab5682953f45a1399a76b6dae0e7"


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v3_paper_source_truth_freezes_exact_authorized_strategy() -> None:
    state = json.loads(_text("PROJECT_STATE.json"))
    assert state["source_of_truth_version"] == "0.14.143"

    paper = state["phase_14_v3_frozen_paper"]
    assert paper["status"] == (
        "REPOSITORY_IMPLEMENTED_AUTHORIZED_PENDING_PRODUCTION_ACTIVATION"
    )
    assert paper["source_v3_research_plan_version"] == "v3-gate-b-preregister-v2"
    assert paper["source_model_artifact_sha256"] == MODEL_SHA
    assert paper["prediction_version"] == "v3-frozen-paper-v1"
    assert paper["execution_version"] == "paper-execution-v3-frozen-v1"
    assert paper["forecast_candidate"] == "single_feature_btc_logistic"
    assert paper["selected_offset_seconds"] == 240
    assert paper["edge_policy"] == "trade_threshold"
    assert paper["min_edge"] == 0.075
    assert paper["fee_rate"] == 0.07
    assert paper["slippage_buffer"] == 0.01
    assert paper["max_selected_book_age_seconds"] == 10
    assert paper["virtual_starting_cash_usd"] == 100
    assert paper["virtual_target_notional_usd"] == 5
    assert paper["simulated_latency_ms"] == 250
    assert paper["simulated_order_ttl_ms"] == 2000
    assert paper["real_money_usd"] == 0
    assert paper["pre_activation_backfill_allowed"] is False
    assert paper["v4_collection_continues"] is True
    assert paper["model_refit_allowed"] is False
    assert paper["threshold_tuning_allowed"] is False
    assert paper["live_order_path_allowed"] is False
    assert paper["automatic_promotion"] is False
    assert paper["live_trading_enabled"] is False
    assert paper["max_trade_size_usd"] == 0
    assert paper["max_daily_loss_usd"] == 0
    assert paper["production_activation_performed"] is False


def test_v3_successor_records_explicit_paper_authorization_without_reopening_holdout() -> None:
    state = json.loads(_text("PROJECT_STATE.json"))
    successor = state["phase_14_btc_first_v3_gate_a"]["successor_gate_b"]

    assert successor["final_holdout_reusable"] is False
    assert successor["paper_activation_authorized"] is True
    assert successor["paper_activation_performed"] is False
    assert successor["paper_model_artifact_sha256"] == MODEL_SHA
    assert successor["paper_selected_forecast_candidate"] == (
        "single_feature_btc_logistic"
    )
    assert successor["paper_selected_offset_seconds"] == 240
    assert successor["paper_selected_min_edge"] == 0.075
    assert successor["paper_real_money_usd"] == 0
    assert successor["paper_model_refit_allowed"] is False
    assert successor["paper_threshold_tuning_allowed"] is False
    assert successor["paper_live_order_path_allowed"] is False


def test_canonical_docs_keep_paper_and_live_money_boundaries_explicit() -> None:
    start = _text("START-HERE.md")
    build = _text("docs/BUILD-ORDER.md")
    master = _text("docs/MASTER-SOURCE-OF-TRUTH.md")
    decisions = _text("docs/DECISION-LOG.md")
    changelog = _text("docs/CHANGELOG.md")
    spec = _text(
        "docs/superpowers/specs/"
        "2026-09-20-phase-14-v3-frozen-paper-activation.md"
    )

    for content in (start, build, master, decisions, changelog, spec):
        assert MODEL_SHA in content
        assert "v3-frozen-paper-v1" in content
        assert "paper-execution-v3-frozen-v1" in content
        assert "0.075" in content
        assert "$0" in content or "zero real money" in content.lower()

    assert "## D-052 —" in decisions
    assert "## 0.14.143 — 20 September 2026" in changelog
    assert "pre-activation" in spec.lower()
    assert "polymarket state is execution-only" in spec.lower()
    assert "v4 regime-aware prospective feature collection continues" in master.lower()
    assert "no wallet" in build.lower()
    assert "do not restart the recorder" in build.lower()
