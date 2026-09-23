from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = (
    ROOT
    / "docs/evidence/phase-15-v3-accelerated-readiness-production-20260923.json"
)


def test_phase15_v3_statistical_readiness_and_geography_pass_for_canary() -> None:
    state = json.loads((ROOT / "PROJECT_STATE.json").read_text(encoding="utf-8"))
    gate = state["phase_15_v3_canary_readiness"]
    master = state["phase_14_checkpoint"]["master_live_gate"]

    assert state["source_of_truth_version"] == "0.14.180"
    assert gate["status"] == (
        "PRODUCTION_READ_ONLY_PASS_STATISTICAL_GATES_PASS_EXECUTION_HOST_BLOCKED"
    )
    assert gate["source_prediction_version"] == "v3-frozen-paper-v1"
    assert gate["source_v3_model_sha256"] == (
        "124627e15cab3997b8abe54ec5237450d976ab5682953f45a1399a76b6dae0e7"
    )
    assert gate["source_selection_sha256"] == (
        "a088c61b291a67866af1c565ee936d9761e6a2936b49f121f616081284dcd508"
    )
    assert gate["strategy_mutation_allowed"] is False
    assert gate["v3_refit_allowed"] is False
    assert gate["recalibration_fit_allowed"] is False
    assert gate["threshold_tuning_allowed"] is False
    assert gate["timing_change_allowed"] is False
    assert gate["statistical_rules_frozen_before_new_reliability_audit"] is True
    assert gate["accelerated_audit_read_only"] is True
    assert gate["accelerated_audit_run_performed"] is True
    assert gate["phase15_candidate"] is True

    assert gate["walk_forward_results_stable_enough"] == "pass"
    assert gate["sufficiently_large_live_paper_sample_with_uncertainty"] == "pass"
    assert gate["positive_after_cost_profitability"] == "pass"
    assert gate["calibration_acceptable"] == "pass"
    assert gate["order_execution_and_reconciliation_tested"] == "pass"

    assert gate["evaluation_count"] == 533
    assert gate["settled_trade_count"] == 77
    assert gate["wins"] == 48
    assert gate["losses"] == 29
    assert gate["mean_pnl_95pct_ci_lower_usd"] > 0
    assert gate["calibration_intercept_95pct_lower"] <= 0 <= (
        gate["calibration_intercept_95pct_upper"]
    )
    assert gate["calibration_slope_95pct_lower"] <= 1 <= (
        gate["calibration_slope_95pct_upper"]
    )

    assert master["walk_forward_results_stable_enough"] == "pass"
    assert master["sufficiently_large_live_paper_sample_with_uncertainty"] == "pass"
    assert master["positive_after_cost_profitability"] == "pass"
    assert master["calibration_acceptable"] == "pass"
    assert master["order_execution_and_reconciliation_tested"] == "pass"
    assert master["explicit_user_live_authorization"] == "pass"
    assert master["geographic_compliance_eligible"] == "pass"

    user_geo = gate["user_physical_network_geoblock"]
    assert user_geo["status"] == "pass"
    assert user_geo["blocked"] is False
    assert user_geo["country"] == "NG"
    assert user_geo["ip_address_persisted"] is False

    candidate = gate["execution_host_candidate"]
    assert candidate["provider"] == "gcp"
    assert candidate["zone"] == "africa-south1-a"
    assert candidate["machine_type"] == "e2-micro"
    assert candidate["status"] == "PROBE_PASS_UNBLOCKED"

    assert gate["phase15_permitted"] is True
    assert gate["live_trading_enabled"] is False
    assert gate["max_trade_size_usd"] == 0
    assert gate["max_daily_loss_usd"] == 0
    assert gate["real_money_mutation_performed"] is False

    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    assert evidence["helper_result"] == "PASS"
    assert evidence["phase15_candidate"] is True
    assert all(value == "pass" for value in evidence["statistical_gates"].values())
    assert evidence["safety"]["real_order_submission_attempted"] is False
    assert evidence["geography"]["overall_geographic_compliance"] == (
        "fail_until_execution_host_unblocked"
    )
