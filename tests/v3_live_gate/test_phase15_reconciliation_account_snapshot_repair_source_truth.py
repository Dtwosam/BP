from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "PROJECT_STATE.json"
HELPER = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase15_v3_reconciliation_account_snapshot_repair_cloudshell.sh"
)
CANARY = ROOT / "src" / "bp_engine" / "execution" / "canary.py"
EVIDENCE = (
    ROOT
    / "docs"
    / "evidence"
    / "phase-15-reconciliation-account-snapshot-bug-diagnosis-20260927.json"
)


def test_reconciliation_account_snapshot_repair_is_ready_but_not_authorized() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    gate = state["phase_15_v3_live_canary"]
    repair = gate["reconciliation_account_snapshot_repair"]

    assert repair["status"] == "READY_NOT_AUTHORIZED"
    assert repair["authorized"] is False
    assert repair["requires_explicit_production_mutation_authorization"] is True
    assert repair["production_mutation_performed"] is False
    assert repair["additional_network_attempts_authorized"] is False
    assert repair["order_submission_authorized"] is False
    assert repair["telegram_approval_authorized_by_repair"] is False
    assert repair["executor_arm_or_invoke_authorized_by_repair"] is False
    assert repair["live_trading_enablement_authorized"] is False
    assert repair["strategy_mutation_authorized"] is False
    assert repair["network_submission_attempt_consumed_by_repair"] is False

    assert repair["observed_trigger_prediction_id"] == (
        "2013df22eb33d69b2f3c1561873037bb1bf49995371c79cc19273af11383b32c"
    )
    assert repair["observed_trigger_paper_order_id"] == (
        "fa92910d12283d266dbd091d1b99f8064905f201c1a7fa6a4358b302b59b61dd"
    )
    assert repair["observed_risk_reasons"] == [
        "liquidity_missing",
        "reconciliation_blocked",
    ]
    assert repair["target_terminal_intent_id"] == (
        "live-intent-4cb75bd0f114e378130b28d7699320e9"
    )
    assert repair["expected_terminal_reconciliation_id"] == (
        "live-reconciliation-745a1e86a0075b4e98cdfbb30517a154"
    )
    assert repair["expected_account_snapshot_source_reconciliation_id"] == (
        "live-reconciliation-81489372163985723f74f2003c7ef1d7"
    )

    bindings = {
        "helper_git_blob_sha": HELPER,
        "canary_git_blob_sha": CANARY,
    }
    for field, path in bindings.items():
        actual = subprocess.run(
            ["git", "hash-object", str(path)],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert repair[field] == actual


def test_reconciliation_repair_helper_is_fail_closed_and_not_self_authorizing() -> None:
    text = HELPER.read_text(encoding="utf-8")

    for marker in (
        "PHASE15_ACCEPT_RECONCILIATION_ACCOUNT_SNAPSHOT_REPAIR",
        "explicit_reconciliation_account_snapshot_repair_authorization_required",
        'repair["status"] == "AUTHORIZED_NOT_EXECUTED"',
        'repair["authorized"] is True',
        "second-canary.attempt.json",
        "executor_not_safe_idle",
        "second_canary_network_attempt_marker_present",
        "before.unresolved_critical_reconciliation == 1",
        "after.unresolved_critical_reconciliation == 0",
        "submission_attempt_consumed_by_repair",
        "network_submission_attempt_consumed_by_repair",
        "ORDER_SUBMISSION_PERFORMED=false",
        "PHASE15_RECONCILIATION_ACCOUNT_SNAPSHOT_REPAIR=PASS",
    ):
        assert marker in text

    for forbidden in (
        "PHASE15_ACCEPT_REAL_MONEY",
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_WALLET_ADDRESS",
        "submit_limit_buy",
        "create_limit_order",
        "post_order",
    ):
        assert forbidden not in text

    completed = subprocess.run(
        ["bash", "-n", str(HELPER)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_bug_diagnosis_evidence_matches_source_truth() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    repair = state["phase_15_v3_live_canary"]["reconciliation_account_snapshot_repair"]
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))

    assert evidence["observed_candidate"]["risk_reasons"] == repair["observed_risk_reasons"]
    assert evidence["observed_candidate"]["prepare_arm_window_seconds"] == "54.90687"
    assert evidence["diagnosis"]["timing_blocked"] is False
    assert evidence["diagnosis"]["watcher_saw_candidate"] is True
    assert evidence["diagnosis"]["live_intent_created"] is False
    assert evidence["repair_design"]["current_db_repair_requires_explicit_authorization"] is True
    assert evidence["repair_design"]["additional_network_attempts_authorized"] is False
    assert evidence["repair_design"]["order_submission_authorized"] is False
    assert evidence["bindings"]["helper_git_blob_sha"] == repair["helper_git_blob_sha"]
    assert evidence["bindings"]["canary_git_blob_sha"] == repair["canary_git_blob_sha"]
    assert evidence["production_mutation_performed"] is False
