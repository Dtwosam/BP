from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODEL_SHA = "124627e15cab3997b8abe54ec5237450d976ab5682953f45a1399a76b6dae0e7"
EVIDENCE = ROOT / "docs/evidence/phase-14-v3-live-gate-reassessment-production-20260923.json"


def test_v3_live_gate_reassessment_source_truth_stays_fail_closed() -> None:
    state = json.loads((ROOT / "PROJECT_STATE.json").read_text(encoding="utf-8"))
    gate = state["phase_14_v3_live_gate_reassessment"]
    master = state["phase_14_checkpoint"]["master_live_gate"]

    assert state["source_of_truth_version"] == "0.14.180"
    assert gate["explicit_user_live_authorization"] == "pass"
    assert gate["prediction_version"] == "v3-frozen-paper-v1"
    assert gate["execution_version"] == "paper-execution-v3-frozen-v1"
    assert gate["model_sha256"] == MODEL_SHA
    assert gate["v3_refit_allowed"] is False
    assert gate["threshold_tuning_allowed"] is False
    assert gate["v4_collection_continues"] is True

    assert gate["status"] == "PRODUCTION_READ_ONLY_PASS_MASTER_GATE_BLOCKED"
    assert gate["production_run_performed"] is True
    assert gate["production_run_status"] == "PASS_READ_ONLY"
    assert gate["settled_trade_count"] == 76
    assert gate["wins"] == 47
    assert gate["losses"] == 29
    assert gate["realized_total_usd"] == "682.252111761097000000"
    assert gate["mean_pnl_95pct_ci_lower_usd"] > 0
    assert gate["profit_factor"] > 1
    assert gate["pnl_excluding_largest_winner_usd"] == "450.802786408456000000"
    assert gate["evaluation_count"] == 519
    assert gate["reconciliation_status"] == "OK"
    assert gate["reconciliation_violation_count"] == 0

    assert gate["profitability_gate"] == "pass"
    assert gate["sample_sufficiency_gate"] == "insufficient_evidence"
    assert gate["calibration_gate"] == "insufficient_evidence"
    assert gate["walk_forward_stability_gate"] == "insufficient_evidence"
    assert gate["geographic_compliance_eligible"] == "fail"
    assert gate["geoblock_blocked"] is True
    assert gate["geoblock_country"] == "US"
    assert gate["geoblock_region"] == "SC"
    assert gate["overall_live_gate"] == "fail"

    assert master["positive_after_cost_profitability"] == "pass"
    assert master["order_execution_and_reconciliation_tested"] == "pass"
    assert master["explicit_user_live_authorization"] == "pass"
    assert master["geographic_compliance_eligible"] == "pass"
    assert master["sufficiently_large_live_paper_sample_with_uncertainty"] == "pass"
    assert master["calibration_acceptable"] == "pass"
    assert master["walk_forward_results_stable_enough"] == "pass"

    assert gate["master_live_gate_mutated"] is True
    assert gate["phase15_permitted"] is False
    assert gate["live_trading_enabled"] is False
    assert gate["max_trade_size_usd"] == 0
    assert gate["max_daily_loss_usd"] == 0
    assert gate["real_money_mutation_performed"] is False

    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    assert evidence["source_main_sha"] == "ba98b3871e03895d04bb2b06d4be5350f6c17491"
    assert evidence["helper_result"] == "PASS"
    assert evidence["v3"]["sample"]["settled_trade_count"] == 76
    assert evidence["v3"]["diagnostics"]["bootstrap_mean_lower_bound_positive"] is True
    assert evidence["geographic_eligibility"]["blocked"] is True
    assert evidence["live_gate_assessment"]["positive_after_cost_profitability"] == "pass"
    assert evidence["live_gate_assessment"]["geographic_compliance_eligible"] == "fail"
    assert evidence["live_gate_assessment"]["overall_live_gate"] == "fail"

    spec_path = (
        ROOT
        / "docs/superpowers/specs/2026-09-23-phase-14-v3-live-gate-reassessment.md"
    )
    spec = spec_path.read_text(encoding="utf-8")
    assert "explicitly authorized pursuing a controlled real-money transition" in spec
    assert "does not override any other Master live-gate row" in spec
    assert "V4 collection continues unchanged" in spec
