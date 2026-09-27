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
    / "phase15_v3_terminal_telegram_intent_reconcile_cloudshell.sh"
)
SAFE_FAIL_EVIDENCE = (
    ROOT
    / "docs"
    / "evidence"
    / "phase-15-v3-fresh-watcher-safe-fail-pending-terminal-intent-20260927.json"
)


def test_terminal_telegram_intent_reconciliation_is_explicitly_authorized() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    gate = state["phase_15_v3_live_canary"]
    recon = gate["terminal_telegram_intent_reconciliation"]
    watch = gate["persistent_prepare_watch"]

    assert recon["status"] == "AUTHORIZED_NOT_RUN"
    assert recon["authorized"] is True
    assert recon["authorization_consumed"] is False
    assert recon["authorization_date"] == "2026-09-27"
    assert recon["authorized_at_main"] == (
        "a06d7520fb0d887d4992bd13c0f3e4b32cb6c208"
    )
    assert recon["authorization_evidence"] == (
        "docs/evidence/phase-15-v3-terminal-telegram-intent-reconciliation-authorization-20260927.json"
    )
    assert recon["intent_id"] == "live-intent-4cb75bd0f114e378130b28d7699320e9"
    assert recon["prediction_id"] == (
        "cca4840dcf6034ac624a48ca3e88e91e4f5d3a96b5d334705e4c1694e449c05c"
    )
    assert recon["paper_order_id"] == (
        "18072fef623e29fa095c433cdd0657958848c5e6a34c194ae6527e532c4dc2ab"
    )
    assert recon["helper"] == (
        "scripts/deploy/phase15_v3_terminal_telegram_intent_reconcile_cloudshell.sh"
    )
    assert recon["helper_git_blob_sha"] == "94f0a3acdaefc03be2b322955101d11427ecf08a"
    assert recon["does_not_authorize_telegram_approve"] is True
    assert recon["does_not_authorize_executor_arm_or_invoke"] is True
    assert recon["does_not_authorize_order_submission"] is True
    assert recon["does_not_consume_second_canary_network_attempt"] is True
    assert recon["failed_intent_retry_allowed"] is False
    assert recon["failed_intent_must_not_be_replayed"] is True
    assert recon["production_result"] == "PENDING_RETRY_AFTER_PRE_MUTATION_SAFE_FAIL"
    assert recon["last_attempt_result"] == (
        "SAFE_FAIL_PRE_MUTATION_LOCAL_BASE64_PORTABILITY"
    )
    assert recon["last_attempt_executor_safety_passed"] is True
    assert recon["last_attempt_second_canary_attempt_marker_absent"] is True
    assert recon["last_attempt_database_precheck_started"] is False
    assert recon["last_attempt_database_mutation_performed"] is False
    assert recon["last_attempt_authorization_consumed"] is False
    assert recon["same_authorization_retry_allowed"] is True
    assert recon["authorization_scope_changed_by_helper_repair"] is False

    assert watch["fresh_restart_authorization_consumed"] is True
    assert watch["fresh_restart_may_not_be_reused"] is True
    assert watch["fresh_restart_requires_new_authorization_after_reconciliation"] is True
    assert watch["second_canary_network_attempt_consumed"] is False


def test_terminal_reconciliation_helper_is_explicit_and_fail_closed() -> None:
    text = HELPER.read_text(encoding="utf-8")
    for marker in (
        "PHASE15_ACCEPT_TERMINAL_INTENT_RECONCILIATION",
        "explicit_terminal_intent_reconciliation_authorization_required",
        'recon.get("status") == "AUTHORIZED_NOT_RUN"',
        'recon.get("authorized") is True',
        'recon.get("authorization_consumed") is False',
        "terminal_intent_reconciliation_helper_binding_mismatch",
        '{"action":"health"}',
        'payload["kill_switch_engaged"] is True',
        'payload["activation_valid"] is False',
        'payload["submission_ready"] is False',
        'payload["live_order_submitted"] is False',
        "/var/lib/bp-canary/telegram-live-handoff/second-canary.attempt.json",
        "default_transaction_read_only=on",
        "submission_attempt_event_count",
        "telegram_handoff_failed_no_retry_before_executor_submission",
        "reconcile_unsubmitted_canary_intent",
        "closed_before_submission",
        "SUBMISSION_ATTEMPT_CONSUMED=false",
        "SECOND_CANARY_NETWORK_ATTEMPT_CONSUMED=false",
        "base64.b64encode",
        "PHASE15_V3_TERMINAL_INTENT_RECONCILIATION=PASS",
    ):
        assert marker in text

    for forbidden in (
        "post_order",
        "create_limit_order",
        "cancel_order",
        '"action":"submit"',
        "PHASE15_ACCEPT_REAL_MONEY",
        "base64 -w0",
        "PHASE15_ACCEPT_TELEGRAM_REAL_MONEY",
    ):
        assert forbidden not in text


def test_terminal_reconciliation_helper_shell_and_embedded_python_are_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(HELPER)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    text = HELPER.read_text(encoding="utf-8")
    blocks = re.findall(r"<<'PY'[^\n]*\n(.*?)\nPY(?:\n|$)", text, flags=re.DOTALL)
    assert len(blocks) >= 7
    for block in blocks:
        ast.parse(block)


def test_fresh_watcher_safe_fail_evidence_preserves_network_slot() -> None:
    evidence = json.loads(SAFE_FAIL_EVIDENCE.read_text(encoding="utf-8"))
    assert evidence["source_main"] == "2fccca2cbb60bc0286fc77ce7dcd19f8dd80dbb5"
    assert evidence["run"]["start_result"] == "PASS"
    assert evidence["run"]["prepare_only"] is True
    assert evidence["run"]["no_real_order_submitted"] is True
    assert evidence["terminal_status"]["status"] == "failed"
    assert evidence["terminal_status"]["reason"] == "canary_prepare_blocked"
    assert evidence["terminal_status"]["last_report"]["reason"] == (
        "pending_live_intent_requires_reconciliation"
    )
    assert evidence["terminal_status"]["submission_attempt_consumed"] is False
    assert evidence["safety"]["second_canary_network_attempt_consumed"] is False
    assert evidence["safety"]["watcher_authorization_consumed"] is True
    assert evidence["safety"]["watcher_restart_may_not_be_reused"] is True
    assert evidence["safety"]["fresh_restart_required_after_reconciliation"] is True
