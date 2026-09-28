from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "PROJECT_STATE.json"
EVIDENCE = (
    ROOT
    / "docs"
    / "evidence"
    / "phase-15-telegram-auto-approver-fixed-scale-spend-repair-production-20260927.json"
)


def test_telegram_auto_approver_fixed_scale_runtime_repair_is_recorded() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    gate = state["phase_15_v3_live_canary"]
    auto = gate["operator_telegram_auto_approver"]
    controlled = gate["controlled_auto_approved_canary_authorization"]
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))

    assert state["live_trading_enabled"] is False
    assert gate["live_trading_enabled"] is False

    assert auto["status"] == "ACTIVE_LIVE_AUTO_APPROVE"
    assert auto["fixed_scale_spend_parser_merge_commit"] == (
        "f93d954f08392fc48cacc57b7b500858c7c4a355"
    )
    assert auto["runtime_repair_status"] == "PASS"
    assert auto["service_state"] == "running"
    assert auto["service_pid"] == 86988
    assert auto["runtime_repair_previous_service_pid"] == 73154
    assert auto["fixed_scale_five_dollar_prompt_verified"] is True
    assert auto["non_five_dollar_prompt_rejected_verified"] is True
    assert auto["bot_identity_confirmed_after_runtime_repair"] is True
    assert auto["runtime_repair_stderr_empty"] is True
    assert auto["runtime_repair_network_submission_attempt_consumed"] is False
    assert auto["runtime_repair_real_order_submitted"] is False
    assert auto["runtime_repair_global_live_trading_enabled"] is False
    assert auto["runtime_repair_scope_expanded"] is False
    assert auto["wrapped_callback_adapter_merge_commit"] == (
        "b6e99ba023c347c86a8c70f431772bb8d3833f2c"
    )
    assert auto["wrapped_callback_adapter_status"] == "PRODUCTION_PASS"
    assert auto["last_successful_approval_message_id"] == 5469

    assert controlled["consumed"] is True
    assert controlled["network_attempt_observed"] is True
    assert controlled["real_order_submission_observed"] is True
    assert controlled["max_network_submission_attempts"] == 1

    assert evidence["result"] == "PASS"
    assert evidence["source_main"] == auto["runtime_repair_source_main"]
    assert evidence["incident"]["intent_id"] == auto["prompt_mismatch_candidate_intent_id"]
    assert evidence["incident"]["network_submission_attempt_consumed"] is False
    assert evidence["production_runtime"]["current_pid"] == 1292
    assert evidence["production_runtime"]["fixed_scale_five_dollar_prompt_test"] == "PASS"
    assert evidence["production_runtime"]["non_five_dollar_prompt_rejection_test"] == "PASS"
    assert evidence["safety"]["order_submission_performed_by_repair"] is False
    assert evidence["safety"]["network_submission_attempt_consumed_by_repair"] is False
    assert evidence["safety"]["additional_network_attempts_authorized"] is False
