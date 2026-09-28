from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "PROJECT_STATE.json"
HELPER = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase15_v3_second_canary_db_reconciliation_cloudshell.sh"
)
EVIDENCE = (
    ROOT
    / "docs"
    / "evidence"
    / "phase-15-v3-second-canary-submission-zero-fill-readonly-20260928.json"
)


def test_second_canary_zero_fill_is_recorded_and_third_order_is_blocked() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    gate = state["phase_15_v3_live_canary"]
    canary = gate["second_live_canary"]
    auth = gate["second_live_canary_authorization"]
    controlled = gate["controlled_auto_approved_canary_authorization"]
    supervisor = gate["controlled_submission_supervisor"]
    recon = gate["second_canary_db_reconciliation"]

    assert state["status"] == "PHASE_15_SECOND_LIVE_CANARY_RECONCILED_ZERO_FILL"
    assert gate["status"] == "SECOND_LIVE_CANARY_RECONCILED_ZERO_FILL"
    assert gate["second_order_authorized"] is False

    assert auth["status"] == "CONSUMED_SUBMITTED_RECONCILIATION_REQUIRED"
    assert auth["consumed"] is True
    assert auth["network_submission_attempt_consumed"] is True
    assert auth["authorization_slot_consumed"] is True
    assert auth["real_order_submitted"] is True
    assert auth["third_order_authorized"] is False

    assert controlled["status"] == "CONSUMED_REAL_SUBMISSION_RECORDED"
    assert controlled["consumed"] is True
    assert controlled["network_attempt_observed"] is True
    assert controlled["real_order_submission_observed"] is True

    assert supervisor["status"] == "TERMINAL_REAL_SUBMISSION_SUCCEEDED"
    assert supervisor["completed"] is True
    assert supervisor["waiting_for_real_submission"] is False
    assert supervisor["third_order_authorized"] is False

    assert canary["intent_id"] == "live-intent-48e0149edbed67572c6e7fab69269dae"
    assert canary["external_order_id"] == (
        "0x127aa37d011dc0d96b5c941e61a6f5fefcd5b7fa56e3eb30f5f6bc77d5eaf4d6"
    )
    assert canary["executor_result"] == "accepted"
    assert canary["cancellation_result"] == "cancelled"
    assert canary["official_fill_state"] == "zero_fill_observed"
    assert canary["confirmed_filled_shares"] == 0
    assert canary["confirmed_filled_notional_usd"] == 0
    assert canary["realized_trade_pnl_usd"] == 0
    assert canary["open_order_count"] == 0
    assert canary["snapshot_stable_across_3_seconds"] is True
    assert canary["external_official_reconciliation_complete"] is True
    assert canary["live_risk_ledger_reconciliation_complete"] is True
    assert canary["db_reconciliation_required"] is False

    assert recon["status"] == "COMPLETED_PASS"
    assert recon["authorized"] is False
    assert recon["authorization_consumed"] is True
    assert recon["production_db_mutation_performed"] is True
    assert recon["last_execution_attempt_status"] == "FAIL_PREWRITE"
    assert recon["last_execution_attempt_production_db_mutation_performed"] is False
    assert recon["fresh_authorization_required_for_rebound_helper"] is False
    assert recon["authorization_rebind_required"] is False
    assert recon["helper_git_blob_sha"] == (
        "1fa22dae91f8ffde5c26c0096f1bf5c53547098e"
    )
    assert recon["live_account_snapshot_runtime_fix_git_blob_sha"] == (
        "3dd9d1ed6821a126e9075f72b2a2e0d6d1f969a6"
    )
    assert recon["live_account_snapshot_runtime_fix_deployed"] is True
    assert recon["live_account_snapshot_runtime_fix_deployment_authorized"] is True
    assert recon["completion_helper_git_blob_sha"] == (
        "272313d9bda1712a9d5afa7fd9b0ea634cde2267"
    )
    assert recon["sidecar_runner_git_blob_sha"] == (
        "ed3ced72c75291a0fd79a15009b0a468561683d9"
    )
    assert recon["sidecar_service_unit_git_blob_sha"] == (
        "2f162c9c17658d6917544b056176a56cc50bea46"
    )
    assert recon["sidecar_canary_git_blob_sha"] == (
        "14c3f508ec1a0c446af4c4ee92572075ffff3753"
    )
    assert recon["prepare_watcher_start_or_restart_authorized"] is False
    assert recon["core_service_restart_authorized"] is False
    assert recon["frozen_v3_runtime_mutation_authorized"] is False
    assert recon["does_not_authorize_order_submission"] is True
    assert recon["does_not_authorize_additional_network_attempt"] is True
    assert recon["production_db_reconciliation_id"] == (
        "live-reconciliation-50ac17663fa46ba96b7d341a68c03c53"
    )
    assert recon["production_db_reconciliation_unresolved_count"] == 0
    assert recon["production_db_reconciliation_critical_count"] == 0
    assert recon["production_db_normalized_event_state"] == (
        "absent_verified_by_executor_receipt"
    )
    assert recon["post_completion_total_exposure_usd"] == 0
    assert recon["post_completion_unresolved_critical_reconciliation"] == 0
    assert recon["completion_result"] == "PASS"
    assert recon["third_order_authorized"] is False


def test_second_canary_readonly_evidence_matches_source_truth() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    gate = state["phase_15_v3_live_canary"]
    canary = gate["second_live_canary"]
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))

    assert evidence["source_main_at_runtime"] == (
        "b6e99ba023c347c86a8c70f431772bb8d3833f2c"
    )
    assert evidence["submission"]["intent_id"] == canary["intent_id"]
    assert evidence["submission"]["external_order_id"] == canary["external_order_id"]
    assert evidence["submission"]["accepted"] is True
    assert evidence["submission"]["network_submission_attempt_consumed"] is True
    assert evidence["submission"]["authorization_slot_consumed"] is True
    assert evidence["submission"]["retry_allowed"] is False
    assert evidence["official_fill_probe"]["read_only"] is True
    assert evidence["official_fill_probe"]["fill_state"] == "zero_fill_observed"
    assert evidence["official_fill_probe"]["official_reconciliation_complete"] is True
    assert evidence["official_fill_probe"]["snapshot_stable_across_3_seconds"] is True
    assert evidence["official_fill_probe"]["open_order_count"] == 0
    assert evidence["official_fill_probe"]["matching_trade_count"] == 0
    assert evidence["interpretation"]["realized_trade_pnl_usd"] == "0"
    assert evidence["interpretation"]["production_db_reconciliation_required"] is True
    assert evidence["safety"]["third_order_authorized"] is False
    assert evidence["safety"]["production_db_mutation_performed_by_this_record"] is False


def test_second_canary_db_reconciliation_helper_is_explicit_and_fail_closed() -> None:
    text = HELPER.read_text(encoding="utf-8")

    for marker in (
        "PHASE15_ACCEPT_SECOND_CANARY_DB_RECONCILIATION",
        "second_canary_db_reconciliation_not_explicitly_accepted",
        'repair["status"] == "AUTHORIZED_READY"',
        'repair["authorized"] is True',
        'repair["authorization_consumed"] is False',
        'authorization["third_order_authorized"] is False',
        'canary["official_fill_state"] == "zero_fill_observed"',
        "*.result.json",
        "expected exactly one exact executor receipt",
        'payload["accepted"] is True',
        'cancellation["cancelled"] is True',
        "absent_verified_by_executor_receipt",
        "normalized order event history is partial or ambiguous",
        "list_open_orders",
        "list_account_trades",
        "snapshot_stable_across_3_seconds",
        "store_reconciliation_run",
        "post_submission_official_zero_fill",
        '"total_exposure_usd": "0"',
        "NETWORK_SUBMISSION_ATTEMPT_CONSUMED_BY_REPAIR=false",
        "TELEGRAM_APPROVAL_PERFORMED=false",
        "EXECUTOR_ARMED=false",
        "EXECUTOR_INVOKED=false",
        "ORDER_SUBMISSION_PERFORMED=false",
        "PHASE15_V3_SECOND_CANARY_DB_RECONCILIATION=PASS",
    ):
        assert marker in text

    for forbidden in (
        "create_limit_order(",
        "post_order(",
        "cancel_order(",
        "store_order_event(",
        "PHASE15_ACCEPT_REAL_MONEY",
    ):
        assert forbidden not in text

    blocks = re.findall(r"<<'PY'[^\n]*\n(.*?)\nPY(?:\n|$)", text, flags=re.DOTALL)
    assert len(blocks) >= 5
    for block in blocks:
        ast.parse(block)

    completed = subprocess.run(
        ["bash", "-n", str(HELPER)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
