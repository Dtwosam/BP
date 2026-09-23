from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_phase15_v3_canary_readiness_is_frozen_and_fail_closed() -> None:
    state = json.loads((ROOT / "PROJECT_STATE.json").read_text(encoding="utf-8"))
    gate = state["phase_15_v3_canary_readiness"]

    assert state["source_of_truth_version"] == "0.14.178"
    assert gate["status"] == "ACCELERATED_READINESS_ENGINEERING_NOT_RUN"
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
    assert gate["accelerated_audit_run_performed"] is False
    assert gate["geographic_gate_separate_and_mandatory"] is True
    assert gate["physical_location_geoblock_check_required"] is True
    assert gate["execution_host_geoblock_check_required"] is True
    assert gate["vpn_proxy_tunnel_bypass_allowed"] is False
    assert gate["phase15_permitted"] is False
    assert gate["live_trading_enabled"] is False
    assert gate["max_trade_size_usd"] == 0
    assert gate["max_daily_loss_usd"] == 0
    assert gate["real_money_mutation_performed"] is False

    spec = (
        ROOT
        / "docs/superpowers/specs/2026-09-23-phase-15-v3-same-day-canary-readiness.md"
    ).read_text(encoding="utf-8")
    assert "frozen before new prospective calibration-reliability diagnostics" in spec
    assert "No new round-number minimum is introduced" in spec
    assert "new reliability diagnostics have not yet been read" in spec
    assert "VPN, proxy, tunnel" in spec
