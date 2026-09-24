from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOST_EVIDENCE = ROOT / "docs/evidence/phase-15-v3-canary-host-geoblock-20260923.json"
LIVE_CANARY_EVIDENCE = ROOT / "docs/evidence/phase-15-v3-first-live-canary-submission-20260924.json"


def test_phase15_first_live_canary_is_submitted_and_reconciliation_is_required() -> None:
    state = json.loads((ROOT / "PROJECT_STATE.json").read_text(encoding="utf-8"))
    gate = state["phase_15_v3_live_canary"]
    master = state["phase_14_checkpoint"]["master_live_gate"]

    assert state["source_of_truth_version"] == "0.14.180"
    assert state["current_phase"] == 15
    assert state["status"] == "PHASE_15_FIRST_LIVE_CANARY_SUBMITTED_RECONCILIATION_REQUIRED"
    assert all(value == "pass" for value in master.values())
    assert state["phase_14_checkpoint"]["overall_live_gate"] == "pass"
    assert state["phase_14_checkpoint"]["phase15_permitted"] is True

    assert gate["status"] == "LIVE_CANARY_SUBMITTED_RECONCILIATION_REQUIRED"
    assert gate["phase15_canary_authorized"] is True
    assert gate["source_prediction_version"] == "v3-frozen-paper-v1"
    assert gate["source_execution_version"] == "paper-execution-v3-frozen-v1"
    assert gate["strategy_target_notional_usd"] == 5
    assert gate["user_authorized_max_trade_size_usd"] == 10
    assert gate["max_trade_size_usd"] == 10
    assert gate["max_total_exposure_usd"] == 10
    assert gate["max_daily_loss_usd"] == 10
    assert gate["max_consecutive_losses"] == 1
    assert gate["max_accepted_orders"] == 1
    assert gate["max_submission_attempts"] == 1
    assert gate["live_min_liquidity_usd"] == 5
    assert gate["official_account_preflight_required"] is True
    assert gate["official_open_order_count_required"] == 0
    assert gate["minimum_collateral_balance_usd"] == 5
    assert gate["activation_binds_exact_intent"] is True
    assert gate["activation_binds_request_sha256"] is True
    assert gate["activation_binds_executor_sha256"] is True
    assert gate["executor_direct_post_order_no_sdk_recovery_retry"] is True
    assert gate["ambiguous_result_retry_allowed"] is False
    assert (
        gate["unsubmitted_intent_reconciliation_helper"]
        == "scripts/deploy/phase15_v3_canary_reconcile_unsubmitted_cloudshell.sh"
    )
    assert gate["pre_submission_closed_event_type"] == "closed_before_submission"
    assert gate["pre_submission_closure_consumes_submission_attempt"] is False
    assert gate["pre_submission_reconciliation_requires_kill_switch_engaged"] is True
    assert gate["pre_submission_reconciliation_requires_activation_invalid"] is True
    assert gate["pre_submission_reconciliation_requires_submission_not_ready"] is True
    assert (
        gate["pre_submission_reconciliation_requires_live_order_submitted_false"] is True
    )
    assert (
        gate["pre_submission_reconciliation_requires_zero_official_open_orders"] is True
    )
    assert gate["order_ttl_seconds"] == 2
    assert gate["historical_trade_reuse_allowed"] is False
    assert gate["wallet_material_allowed_on_us_host"] is False
    assert gate["automated_real_money_submission"] is False
    assert gate["manual_real_money_submission_required"] is True
    assert gate["live_trading_enabled"] is False
    assert gate["canary_order_submitted"] is True
    assert gate["reconciliation_required_before_second_order"] is True
    assert gate["second_order_authorized"] is False
    first = gate["first_live_canary"]
    assert first["status"] == "SUBMITTED_AND_RECORDED_RECONCILIATION_PENDING"
    assert first["intent_id"] == "live-intent-6cdfcfd28d0eb52f1ee0762bfd351409"
    assert first["external_order_id"] == (
        "0x7c85e5e8753a875dfd9fec8ffd45726164863648127351b21c2a8ba1819a28de"
    )
    assert first["target_notional_usd"] == 5
    assert first["executor_result"] == "accepted"
    assert first["cancellation_result"] == "cancelled"
    assert first["network_submission_attempt_consumed"] is True
    assert first["retry_authorized"] is False
    assert first["second_order_authorized"] is False
    assert first["official_order_fill_reconciliation_required"] is True
    assert first["official_order_fill_reconciliation_status"] == "PENDING"

    live_evidence = json.loads(LIVE_CANARY_EVIDENCE.read_text(encoding="utf-8"))
    assert live_evidence["status"] == "SUBMITTED_AND_RECORDED_RECONCILIATION_PENDING"
    assert live_evidence["submission_result"]["accepted"] is True
    assert live_evidence["submission_result"]["cancellation"]["cancelled"] is True
    assert live_evidence["record_result"]["event_type"] == "accepted"
    assert live_evidence["safety"]["exactly_one_network_submission_attempt_consumed"] is True
    assert live_evidence["safety"]["retry_authorized"] is False
    assert live_evidence["reconciliation"]["required"] is True
    assert live_evidence["reconciliation"]["status"] == "PENDING"

    evidence = json.loads(HOST_EVIDENCE.read_text(encoding="utf-8"))
    assert evidence["helper_result"] == "PASS"
    assert evidence["direct_geoblock"]["blocked"] is False
    assert evidence["direct_geoblock"]["country"] == "ZA"
    assert evidence["safety"]["trading_software_installed"] is False
    assert evidence["safety"]["wallet_or_signing_material_present"] is False
