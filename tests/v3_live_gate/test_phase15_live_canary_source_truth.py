from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOST_EVIDENCE = ROOT / "docs/evidence/phase-15-v3-canary-host-geoblock-20260923.json"
LIVE_CANARY_EVIDENCE = ROOT / "docs/evidence/phase-15-v3-first-live-canary-submission-20260924.json"


def test_phase15_second_live_canary_is_telegram_authorized_but_not_submitted() -> None:
    state = json.loads((ROOT / "PROJECT_STATE.json").read_text(encoding="utf-8"))
    gate = state["phase_15_v3_live_canary"]
    master = state["phase_14_checkpoint"]["master_live_gate"]

    assert state["source_of_truth_version"] == "0.14.180"
    assert state["current_phase"] == 15
    assert state["status"] == "PHASE_15_SECOND_LIVE_CANARY_TELEGRAM_AUTHORIZED_NOT_SUBMITTED"
    assert all(value == "pass" for value in master.values())
    assert state["phase_14_checkpoint"]["overall_live_gate"] == "pass"
    assert state["phase_14_checkpoint"]["phase15_permitted"] is True

    assert gate["status"] == "SECOND_LIVE_CANARY_TELEGRAM_AUTHORIZED_NOT_SUBMITTED"
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
    assert gate["automated_real_money_submission"] is True
    assert gate["manual_real_money_submission_required"] is False
    assert gate["live_trading_enabled"] is False
    assert gate["canary_order_submitted"] is True
    assert gate["reconciliation_required_before_second_order"] is True
    assert gate["second_order_authorized"] is True
    assert gate["telegram_one_tap_submission_authorized"] is True
    assert gate["telegram_persistent_execution_transport_authorized"] is True
    assert gate["telegram_pubsub_transport_authorized"] is True
    second = gate["second_live_canary_authorization"]
    assert second["status"] == "AUTHORIZED_NOT_SUBMITTED"
    assert second["strategy_target_notional_usd"] == 5
    assert second["hard_max_trade_size_usd"] == 10
    assert second["max_network_submission_attempts"] == 1
    assert second["global_attempt_marker_is_one_shot"] is True
    assert second["global_attempt_marker_path"] == (
        "/var/lib/bp-canary/telegram-live-handoff/second-canary.attempt.json"
    )
    assert second["requires_fresh_telegram_approval"] is True
    assert second["requires_execution_package_verifier_pass"] is True
    assert second["requires_privileged_handoff_contract_verifier_pass"] is True
    assert second["requires_official_reconciliation_before_any_third_order"] is True
    assert second["retry_on_missing_malformed_or_ambiguous_result"] is False
    assert second["stake_growth_authorized"] is False
    assert second["v3_strategy_mutation_authorized"] is False
    assert second["v4_mutation_authorized"] is False
    assert second["broad_autonomous_live_rollout_authorized"] is False
    first = gate["first_live_canary"]
    assert first["status"] == "RECONCILED_ZERO_FILL"
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
    assert first["official_order_fill_reconciliation_status"] == "PASS_ZERO_FILL"
    assert first["confirmed_filled_shares"] == 0
    assert first["confirmed_filled_notional_usd"] == 0
    assert first["confirmed_fill_fraction_of_requested"] == 0
    assert first["exposure_usd"] == 0
    assert first["realized_trade_pnl_usd"] == 0
    assert first["fill_quality_observed"] is False
    assert first["slippage_observed"] is False
    assert first["submission_path_validated"] is True
    assert first["cancellation_path_validated"] is True
    assert first["official_fill_probe_result"] == "PASS"
    assert first["official_fill_state"] == "zero_fill_observed"
    assert first["official_reconciliation_complete"] is True

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

def test_phase15_telegram_transport_activation_is_authorized_but_not_an_order() -> None:
    state = json.loads((ROOT / "PROJECT_STATE.json").read_text(encoding="utf-8"))
    gate = state["phase_15_v3_live_canary"]
    stage = gate["telegram_transport_stage"]
    activation = gate["telegram_transport_activation_authorization"]

    assert stage["status"] == "PRODUCTION_STAGED_INACTIVE"
    assert stage["stage_id"] == "phase15-telegram-stage-05b83214b159772872bc7347"
    assert stage["release_head"] == "ba0bc324cab50bf0809c8eed4e019c13bc4c6653"
    assert stage["activation_authorized"] is True
    assert stage["activation_reauthorization_required"] is False
    assert stage["restage_required_before_activation_retry"] is False
    assert activation["status"] == "AUTHORIZED_NOT_ACTIVATED"
    assert activation["authorized_stage_id"] == stage["stage_id"]
    assert activation["authorized_at_main"] == "3852c19aaa47be6335f3475b873bb04aa84be30f"
    assert activation["activation_helper_git_blob_sha"] == (
        "d2560354d10c9964ba0a5a0161f1be9ffb2dfd86"
    )
    assert activation["fresh_explicit_authorization_required_for_current_stage"] is False
    assert activation["does_not_submit_real_order"] is True
    assert activation["fresh_private_telegram_approval_still_required_for_second_canary"] is True
    assert activation["global_second_canary_attempt_marker_still_one_shot"] is True
    assert activation["official_reconciliation_required_before_any_third_order"] is True
    assert activation["broad_autonomous_live_rollout_authorized"] is False
    assert state["live_trading_enabled"] is False
    assert gate["live_trading_enabled"] is False

def test_phase15_telegram_approval_listener_runtime_repair_is_required() -> None:
    state = json.loads((ROOT / "PROJECT_STATE.json").read_text(encoding="utf-8"))
    gate = state["phase_15_v3_live_canary"]
    listener = gate["telegram_approval_listener_install_authorization"]
    activation = gate["telegram_transport_activation_authorization"]

    assert listener["status"] == "RUNTIME_REPAIR_AUTHORIZED"
    assert listener["target_host"] == "bp-recorder"
    assert listener["installation_type"] == "FIRST_INSTALL"
    assert listener["deployed_head"] == "13b3f358543ef7a26c31bea65ec57b68f9ebf45f"
    assert listener["runtime_health_status"] == "RESTART_LOOP"
    assert listener["runtime_failure_reason"] == "telegram_approval_module_import_failed"
    assert listener["observed_restart_count"] == 1749
    assert listener["service_active"] is True
    assert listener["service_active_observed_transient"] is True
    assert listener["service_healthy"] is False
    assert listener["service_enabled"] is True
    assert listener["listener_binding_current"] is False
    assert listener["handoff_configured"] is False
    assert listener["handoff_env_present"] is False
    assert listener["runtime_repair_required"] is True
    assert listener["runtime_repair_authorized"] is True
    assert listener["runtime_repair_authorized_at_main"] == "a60ff465ab307a360fb20fcefa5158f8258e46a8"
    assert listener["runtime_repair_helper_git_blob_sha"] == (
        "028c5e0f747f388d8471b3be49ba288b9bbbecc4"
    )
    assert listener["no_real_order_submitted"] is True
    assert listener["stores_bot_token_in_git"] is False
    assert listener["live_trading_enabled_during_install"] is False
    assert listener["real_order_submission_authorized_by_install"] is False
    assert activation["status"] == "AUTHORIZED_NOT_ACTIVATED"
    assert activation["does_not_submit_real_order"] is True
    assert listener["runtime_failure_evidence"] == (
        "docs/evidence/phase-15-v3-telegram-approval-listener-runtime-failure-20260926.json"
    )

def test_phase15_iap_ssh_api_enablement_is_narrowly_authorized() -> None:
    state = json.loads((ROOT / "PROJECT_STATE.json").read_text(encoding="utf-8"))
    gate = state["phase_15_v3_live_canary"]
    iap = gate["iap_ssh_access_authorization"]

    assert iap["status"] == "AUTHORIZED_NOT_ENABLED"
    assert iap["project_id"] == "project-4397f2c0-7098-4c1c-abb"
    assert iap["service"] == "iap.googleapis.com"
    assert iap["does_not_authorize_firewall_change"] is True
    assert iap["does_not_authorize_vm_mutation"] is True
    assert iap["does_not_authorize_transport_activation"] is True
    assert iap["does_not_authorize_order_submission"] is True
