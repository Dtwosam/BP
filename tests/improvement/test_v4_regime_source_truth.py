from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
V4_SPEC = (
    "docs/superpowers/specs/"
    "2026-09-20-phase-14-v4-regime-aware-challenger.md"
)
V3_HOLDOUT_EVIDENCE = (
    "docs/evidence/"
    "phase-14-v3-gate-b-successor-final-holdout-20260920.json"
)


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v4_source_truth_is_separate_and_prospective() -> None:
    state = json.loads(_text("PROJECT_STATE.json"))
    assert state["source_of_truth_version"] == "0.14.145"

    v4 = state["phase_14_v4_regime_aware"]
    assert v4["feature_version"] == "core-v4-regime-aware"
    assert v4["dataset_version"] == "supervised-core-v4-regime-aware-v1"
    assert v4["label_version"] == "official-outcome-v1"
    assert v4["horizon_seconds"] == 300
    assert v4["offsets_seconds"] == [60, 120, 180, 240]
    assert v4["regime_lookbacks_seconds"] == [300, 900, 3600]
    assert v4["regime_names"] == ["bull", "bear", "sideways_mixed", "unknown"]
    assert v4["polymarket_predictor_keys_allowed"] is False
    assert v4["v3_final_holdout_tuning_allowed"] is False
    assert v4["prospective_gate_b_required"] is True
    assert v4["successor_scope"] == "COMPREHENSIVE_V3_WEAKNESS_REMEDIATION"
    assert v4["regime_awareness_is_only_one_objective"] is True
    assert v4["weakness_remediation_objectives"] == [
        "regime_dependence",
        "trade_side_asymmetry",
        "selected_model_simplicity_and_feature_underuse",
        "calibration_robustness",
        "timing_dependence",
        "trade_quality_vs_coverage",
        "loss_drawdown_robustness",
        "execution_availability",
    ]
    assert v4["future_gate_b_required_model_families"] == [
        "simple_baseline",
        "multivariate_btc_native",
        "nonlinear_btc_native",
    ]
    assert v4["future_gate_b_required_offsets_seconds"] == [60, 120, 180, 240]
    assert v4["future_gate_b_must_report_coverage_frontier"] is True
    assert v4["future_gate_b_must_report_drawdown_and_losing_streak"] is True
    assert v4["future_gate_b_must_separate_execution_from_forecast_quality"] is True
    assert v4["v3_holdout_may_only_motivate_hypotheses"] is True
    assert v4["v3_holdout_numeric_tuning_allowed"] is False
    assert v4["current_collector_change_required"] is False
    assert v4["production_materialization_authorized"] is True
    assert v4["production_collection_authorized"] is True
    assert v4["prospective_collection_epoch_start"] == "2026-09-20T12:40:53Z"
    assert v4["collector_preserves_deployed_checkout"] is True
    assert v4["collector_restarts_recorder"] is False
    assert v4["production_materialization_performed"] is True
    assert v4["production_collector_enabled"] is True
    assert v4["production_collector_active"] is True
    assert v4["production_rollout_passed"] is True
    assert v4["initial_coverage_market_count"] == 9
    assert v4["initial_coverage_row_count"] == 36
    assert v4["initial_bull_market_count"] == 2
    assert v4["initial_bear_market_count"] == 0
    assert v4["initial_sideways_mixed_market_count"] == 7
    assert v4["initial_unknown_market_count"] == 0
    assert v4["initial_future_cutoff_violation_count"] == 0
    assert v4["initial_polymarket_predictor_key_count"] == 0
    assert v4["initial_regime_invariant_violation_count"] == 0
    assert v4["training_performed"] is False
    assert v4["final_holdout_access_performed"] is False
    assert v4["paper_activation_performed"] is False
    assert v4["automatic_promotion"] is False
    assert v4["live_trading_enabled"] is False
    assert v4["max_trade_size_usd"] == 0
    assert v4["max_daily_loss_usd"] == 0


def test_consumed_v3_holdout_is_durable_motivation_not_v4_tuning_data() -> None:
    evidence = json.loads(_text(V3_HOLDOUT_EVIDENCE))
    assert evidence["status"] == "V3_GATE_B_SUCCESSOR_FINAL_HOLDOUT_EVALUATED"
    assert evidence["final_holdout"]["market_count"] == 144
    assert evidence["final_holdout"]["trade_count"] == 20
    assert evidence["final_holdout"]["realized_pnl_after_assumed_costs"] == 1.654224
    assert evidence["trade_ledger_summary"]["by_side"]["up"]["wins"] == 7
    assert evidence["trade_ledger_summary"]["by_side"]["down"]["wins"] == 2
    assert evidence["safety"]["model_refit_performed"] is False
    assert evidence["safety"]["automatic_promotion"] is False
    assert evidence["safety"]["activation_performed"] is False
    assert evidence["interpretation"]["final_holdout_consumed"] is True
    assert evidence["interpretation"]["reusable_for_future_tuning"] is False
    assert evidence["interpretation"]["v4_motivation_only"] is True


def test_v4_design_freezes_regime_definition_and_safety_boundary() -> None:
    spec = _text(V4_SPEC).lower()
    for required in (
        "core-v4-regime-aware",
        "supervised-core-v4-regime-aware-v1",
        "return_5m",
        "return_15m",
        "return_60m",
        "bull",
        "bear",
        "sideways_mixed",
        "unknown",
        "majority sign",
        "v3 final holdout is permanently consumed",
        "must not be used",
        "new prospective cohort",
        "active in research mode",
        "model fitting",
        "paper activation",
        "live trading",
        "v3 weakness-remediation objectives",
        "trade-side asymmetry",
        "selected-model simplicity / feature underuse",
        "calibration robustness",
        "timing dependence",
        "trade-quality versus coverage",
        "loss/drawdown robustness",
        "execution availability",
        "simple baseline",
        "multivariate btc-native models",
        "nonlinear challenger",
    ):
        assert required in spec


def test_v4_collection_remains_active_during_frozen_v3_paper_activation() -> None:
    start = _text("START-HERE.md")
    build = _text("docs/BUILD-ORDER.md")
    master = _text("docs/MASTER-SOURCE-OF-TRUTH.md")
    decisions = _text("docs/DECISION-LOG.md")
    changelog = _text("docs/CHANGELOG.md")

    assert "core-v4-regime-aware" in master
    assert "V4 regime-aware" in start
    assert "V4 regime-aware feature collection" in build
    assert "V4 regime-aware collection continues in parallel" in master

    assert "## D-049 —" in decisions
    assert "## D-050 —" in decisions
    assert "## D-051 —" in decisions
    assert "## D-052 —" in decisions
    assert "## D-053 —" in decisions
    assert "## D-054 —" in decisions
    assert "## 0.14.145 — 20 September 2026" in changelog
    assert "automatic promotion" in master.lower()
    assert "live trading" in master.lower()
