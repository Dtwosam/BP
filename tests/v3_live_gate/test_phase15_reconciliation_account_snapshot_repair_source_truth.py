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
LIVE = ROOT / "src" / "bp_engine" / "execution" / "live.py"
EVIDENCE = (
    ROOT
    / "docs"
    / "evidence"
    / "phase-15-reconciliation-account-snapshot-bug-diagnosis-20260927.json"
)
AUTHORIZATION_EVIDENCE = (
    ROOT
    / "docs"
    / "evidence"
    / "phase-15-reconciliation-account-snapshot-repair-authorization-20260927.json"
)
RUNTIME_COMPAT_EVIDENCE = (
    ROOT
    / "docs"
    / "evidence"
    / "phase-15-reconciliation-account-snapshot-repair-runtime-compat-20260927.json"
)
COMPLETION_EVIDENCE = (
    ROOT
    / "docs"
    / "evidence"
    / "phase-15-reconciliation-account-snapshot-repair-production-pass-20260927.json"
)


def test_reconciliation_account_snapshot_repair_production_pass() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    gate = state["phase_15_v3_live_canary"]
    repair = gate["reconciliation_account_snapshot_repair"]

    assert repair["status"] == "PRODUCTION_PASS"
    assert repair["authorized"] is False
    assert repair["authorization_consumed"] is True
    assert repair["authorization_received_date"] == "2026-09-27"
    assert repair["authorization_scope"] == (
        "One-time production reconciliation account-snapshot repair only; no order "
        "submission, no network attempt, no executor arm/invoke, no Telegram approval, "
        "no live-trading expansion, and no strategy mutation."
    )
    assert repair["authorization_evidence"] == (
        "docs/evidence/"
        "phase-15-reconciliation-account-snapshot-repair-authorization-20260927.json"
    )
    assert repair["requires_explicit_production_mutation_authorization"] is True
    assert repair["production_mutation_performed"] is True
    assert repair["additional_network_attempts_authorized"] is False
    assert repair["order_submission_authorized"] is False
    assert repair["telegram_approval_authorized_by_repair"] is False
    assert repair["executor_arm_or_invoke_authorized_by_repair"] is False
    assert repair["live_trading_enablement_authorized"] is False
    assert repair["strategy_mutation_authorized"] is False
    assert repair["network_submission_attempt_consumed_by_repair"] is False
    assert repair["completion_evidence"] == (
        "docs/evidence/"
        "phase-15-reconciliation-account-snapshot-repair-production-pass-20260927.json"
    )
    assert repair["new_reconciliation_id"] == (
        "live-reconciliation-5f601ea527ff9776ea905302e862ce27"
    )
    assert repair["carried_from_reconciliation_id"] == (
        "live-reconciliation-81489372163985723f74f2003c7ef1d7"
    )
    assert repair["before_unresolved_critical_reconciliation"] == 1
    assert repair["after_unresolved_critical_reconciliation"] == 0
    assert repair["last_execution_attempt_status"] == "PRODUCTION_PASS"
    assert repair["last_execution_attempt_production_mutation_performed"] is True

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

    assert repair["live_git_blob_sha"] == (
        "0617ffeda8365cdd6ab2636af00e58ca8954c4db"
    )
    current_live = subprocess.run(
        ["git", "hash-object", str(LIVE)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    second_repair = gate["second_canary_db_reconciliation"]
    assert current_live == second_repair["live_account_snapshot_runtime_fix_git_blob_sha"]
    assert current_live != repair["live_git_blob_sha"]
    assert second_repair["live_account_snapshot_runtime_fix_deployed"] is True


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
        "LIVE_SOURCE_B64",
        "CANARY_SOURCE_B64",
        'types.ModuleType("bp_engine.execution.live")',
        'types.ModuleType("phase15_repair_canary_inline")',
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
        "from bp_engine.execution import canary",
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
    assert evidence["bindings"]["helper_git_blob_sha"] == (
        "c62d97971fef62ca1418363449035cf69d1854aa"
    )
    assert evidence["bindings"]["helper_git_blob_sha"] != repair["helper_git_blob_sha"]
    assert evidence["bindings"]["canary_git_blob_sha"] == repair["canary_git_blob_sha"]
    assert evidence["production_mutation_performed"] is False


def test_reconciliation_repair_authorization_evidence_matches_source_truth() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    repair = state["phase_15_v3_live_canary"]["reconciliation_account_snapshot_repair"]
    evidence = json.loads(AUTHORIZATION_EVIDENCE.read_text(encoding="utf-8"))

    assert evidence["source_main"] == "5738b56366a256f841c2c04219b4e478c8b95946"
    assert evidence["status"] == "AUTHORIZED_NOT_EXECUTED"
    assert evidence["authorization"]["received"] is True
    assert evidence["authorization"]["order_submission_authorized"] is False
    assert evidence["authorization"]["network_submission_attempt_authorized"] is False
    assert evidence["authorization"]["executor_arm_or_invoke_authorized"] is False
    assert evidence["authorization"]["telegram_approval_authorized"] is False
    assert evidence["authorization"]["live_trading_enablement_authorized"] is False
    assert evidence["authorization"]["strategy_mutation_authorized"] is False
    assert evidence["authorization"]["additional_network_attempts_authorized"] is False
    assert evidence["target"]["intent_id"] == repair["target_terminal_intent_id"]
    assert evidence["bindings"]["helper_git_blob_sha"] == (
        "c62d97971fef62ca1418363449035cf69d1854aa"
    )
    assert evidence["bindings"]["helper_git_blob_sha"] != repair["helper_git_blob_sha"]
    assert evidence["bindings"]["canary_git_blob_sha"] == repair["canary_git_blob_sha"]
    assert evidence["production_mutation_performed"] is False


def test_reconciliation_repair_runtime_compat_evidence_matches_source_truth() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    repair = state["phase_15_v3_live_canary"]["reconciliation_account_snapshot_repair"]
    evidence = json.loads(RUNTIME_COMPAT_EVIDENCE.read_text(encoding="utf-8"))

    assert evidence["source_main_before_repair"] == (
        "f7ad4b0ae17dc0c0e7b313a8436ba2f659549001"
    )
    failed = evidence["failed_execution"]
    assert failed["helper_result"] == "FAIL"
    assert failed["reason"] == "database_repair_failed"
    assert failed["production_mutation_performed"] is False
    assert failed["reconciliation_row_written"] is False
    assert failed["order_submission_performed"] is False
    assert failed["network_submission_attempt_consumed"] is False
    assert evidence["authorization_preserved"]["status"] == "AUTHORIZED_NOT_EXECUTED"
    assert evidence["authorization_preserved"]["scope_changed"] is False
    assert evidence["bindings"]["repaired_helper_git_blob_sha"] == repair["helper_git_blob_sha"]
    assert evidence["bindings"]["canary_git_blob_sha"] == repair["canary_git_blob_sha"]
    assert evidence["bindings"]["live_git_blob_sha"] == repair["live_git_blob_sha"]
    assert repair["last_execution_attempt_status"] == "PRODUCTION_PASS"
    assert repair["last_execution_attempt_production_mutation_performed"] is True


def test_reconciliation_repair_completion_evidence_matches_source_truth() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    repair = state["phase_15_v3_live_canary"]["reconciliation_account_snapshot_repair"]
    evidence = json.loads(COMPLETION_EVIDENCE.read_text(encoding="utf-8"))

    assert evidence["executed_from_main"] == (
        "88c14929eeddec51910386aabf18b9d54e8175c5"
    )
    assert evidence["result"] == "PASS"
    result = evidence["repair"]
    assert result["status"] == "repaired"
    assert result["target_intent_id"] == repair["target_terminal_intent_id"]
    assert result["new_reconciliation_id"] == repair["new_reconciliation_id"]
    assert (
        result["carried_from_reconciliation_id"]
        == repair["carried_from_reconciliation_id"]
    )
    assert (
        result["before_unresolved_critical_reconciliation"]
        == repair["before_unresolved_critical_reconciliation"]
    )
    assert (
        result["after_unresolved_critical_reconciliation"]
        == repair["after_unresolved_critical_reconciliation"]
    )
    assert evidence["safety"]["order_submission_performed"] is False
    assert evidence["safety"]["submission_attempt_consumed_by_repair"] is False
    assert evidence["safety"]["network_submission_attempt_consumed_by_repair"] is False
    assert evidence["safety"]["live_trading_enablement_performed"] is False
    assert evidence["safety"]["strategy_mutation_performed"] is False
    assert evidence["production_mutation_performed"] is True
