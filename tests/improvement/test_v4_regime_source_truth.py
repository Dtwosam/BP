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
    assert state["source_of_truth_version"] == "0.14.141"

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
    assert v4["production_materialization_authorized"] is True
    assert v4["production_collection_authorized"] is True
    assert v4["prospective_collection_epoch_start"] == "2026-09-20T12:40:53Z"
    assert v4["collector_preserves_deployed_checkout"] is True
    assert v4["collector_restarts_recorder"] is False
    assert v4["production_materialization_performed"] is False
    assert v4["production_collector_enabled"] is False
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
        "production database writes",
        "model training",
        "paper activation",
        "live trading",
    ):
        assert required in spec


def test_canonical_handoff_points_to_v4_and_keeps_activation_blocked() -> None:
    start = _text("START-HERE.md")
    build = _text("docs/BUILD-ORDER.md")
    master = _text("docs/MASTER-SOURCE-OF-TRUTH.md")
    decisions = _text("docs/DECISION-LOG.md")
    changelog = _text("docs/CHANGELOG.md")

    for content in (start, build, master, decisions, changelog):
        assert "core-v4-regime-aware" in content
        assert "V3" in content or "v3" in content
        assert "holdout" in content.lower()

    assert "## D-049 —" in decisions
    assert "## D-050 —" in decisions
    assert "## 0.14.141 — 20 September 2026" in changelog
    assert "production feature materialization and collection" in start.lower()
    assert "2026-09-20t12:40:53z" in start.lower()
    assert "2026-09-20t12:40:53z" in build.lower()
    assert "leave `/opt/bp` unchanged" in build.lower()
    assert "automatic promotion" in master.lower()
    assert "live trading" in master.lower()
