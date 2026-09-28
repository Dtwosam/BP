from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "PROJECT_STATE.json"
EVIDENCE = (
    ROOT
    / "docs"
    / "evidence"
    / "phase-15-telegram-auto-approver-keyboard-diagnostic-production-20260928.json"
)


def test_keyboard_diagnostic_production_deployment_is_recorded() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    gate = state["phase_15_v3_live_canary"]
    auto = gate["operator_telegram_auto_approver"]
    controlled = gate["controlled_auto_approved_canary_authorization"]
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))

    assert state["live_trading_enabled"] is False
    assert gate["live_trading_enabled"] is False

    assert auto["status"] == "ACTIVE_LIVE_AUTO_APPROVE"
    assert auto["keyboard_diagnostic_merge_commit"] == (
        "85286e91f903797b02aa064742dcbae79fd06a94"
    )
    assert auto["keyboard_diagnostic_runtime_status"] == "ACTIVE_DIAGNOSTIC"
    assert auto["keyboard_diagnostic_previous_pid"] == 1292
    assert auto["keyboard_diagnostic_current_pid"] == 69385
    assert auto["keyboard_diagnostic_service_started"] is True
    assert auto["keyboard_diagnostic_telegram_connection_state"] == "established"
    assert auto["keyboard_diagnostic_acceptance_rules_relaxed"] is False
    assert auto["keyboard_diagnostic_network_submission_attempt_consumed"] is False
    assert auto["keyboard_diagnostic_real_order_submitted"] is False
    assert auto["keyboard_diagnostic_scope_expanded"] is False

    assert controlled["max_network_submission_attempts"] == 1
    assert controlled["consumed"] is False

    assert evidence["result"] == "PASS"
    assert evidence["source_main"] == auto["keyboard_diagnostic_merge_commit"]
    assert evidence["deployment"]["post_restart_pid"] == 69385
    assert evidence["runtime"]["service_started"] is True
    assert evidence["runtime"]["tcp_connection"]["state"] == "ESTABLISHED"
    assert evidence["runtime"]["telegram_session"]["dc_id"] == 4
    assert evidence["safety"]["keyboard_acceptance_rules_relaxed"] is False
    assert evidence["safety"]["network_submission_attempt_consumed_by_deployment"] is False
