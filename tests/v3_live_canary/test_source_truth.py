from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_phase15_single_order_canary_source_truth_is_exact_and_not_deployed() -> None:
    state = json.loads((ROOT / "PROJECT_STATE.json").read_text(encoding="utf-8"))
    master = state["phase_14_checkpoint"]["master_live_gate"]
    canary = state["phase_15_v3_single_order_canary"]

    assert state["source_of_truth_version"] == "0.15.0"
    assert state["current_phase"] == 15
    assert state["phase_14_checkpoint"]["overall_live_gate"] == "pass"
    assert state["phase_14_checkpoint"]["live_gate_eligible"] is True
    assert state["phase_14_checkpoint"]["phase15_permitted"] is True
    assert all(value == "pass" for value in master.values())

    assert canary["status"] == "ENGINEERING_NOT_DEPLOYED"
    assert canary["execution_version"] == "live-execution-v3-canary-v1"
    assert canary["risk_policy_version"] == "live-risk-v3-canary-v1"
    assert canary["source_prediction_version"] == "v3-frozen-paper-v1"
    assert canary["target_notional_usd"] == "5.00"
    assert canary["max_trade_size_usd"] == "5.00"
    assert canary["max_total_exposure_usd"] == "5.00"
    assert canary["max_daily_loss_usd"] == "5.00"
    assert canary["max_consecutive_losses"] == 1
    assert canary["min_edge"] == "0.075"
    assert canary["max_external_submission_attempts"] == 1
    assert canary["post_submit_cancel_after_ms"] == 2000
    assert canary["user_geography"] == {
        "blocked": False,
        "country": "NG",
        "region": "LA",
    }
    assert canary["execution_host_geography"] == {
        "blocked": False,
        "country": "ZA",
        "region": "GP",
    }
    assert canary["existing_us_host_authenticated_order_submission_allowed"] is False
    assert canary["trading_secret_on_us_host"] is False
    assert canary["deployment_performed"] is False
    assert canary["live_order_attempted"] is False
    assert canary["live_trading_enabled"] is False
    assert canary["real_money_mutation_performed"] is False
