from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "PROJECT_STATE.json"
SUPERVISOR = ROOT / "ops" / "phase15_submission_supervisor" / "run.py"
RECONCILE = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase15_v3_controlled_canary_reconcile_unsubmitted_cloudshell.sh"
)
START = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase15_v3_canary_prepare_watch_start_cloudshell.sh"
)
EVIDENCE = (
    ROOT
    / "docs"
    / "evidence"
    / "phase-15-controlled-submission-supervisor-authorization-20260927.json"
)


def test_submission_supervisor_source_truth_is_narrow_and_bound() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    gate = state["phase_15_v3_live_canary"]
    second = gate["second_live_canary_authorization"]
    controlled = gate["controlled_auto_approved_canary_authorization"]
    supervisor = gate["controlled_submission_supervisor"]

    assert second["status"] == "AUTHORIZED_NOT_SUBMITTED"
    assert second["max_network_submission_attempts"] == 1
    assert controlled["consumed"] is False
    assert controlled["completion_condition"] == "real_five_dollar_submission_recorded"
    assert controlled["candidate_preparation_is_completion"] is False
    assert controlled["pre_network_candidate_recycling_authorized"] is True

    assert supervisor["status"] == "AUTHORIZED_NOT_DEPLOYED"
    assert supervisor["authorized"] is True
    assert supervisor["completed"] is False
    assert supervisor["target_notional_usd"] == 5
    assert supervisor["max_network_submission_attempts"] == 1
    assert supervisor["candidate_recycle_allowed_before_network_attempt"] is True
    assert supervisor["additional_network_attempts_authorized"] is False
    assert supervisor["stop_on_network_attempt_without_clear_success"] is True
    assert supervisor["stop_on_ambiguous_network_attempt"] is True
    assert supervisor["ambiguous_attempt_grace_seconds"] == 45
    assert supervisor["reconcile_only_after_market_end_grace_seconds"] == 20
    assert supervisor["reconcile_requires_global_attempt_marker_absent"] is True
    assert supervisor["reconcile_requires_executor_safe_idle"] is True
    assert supervisor["reconcile_event_type"] == "closed_before_submission"
    assert supervisor["reconcile_consumes_network_attempt"] is False
    assert supervisor["global_live_trading_enablement_authorized"] is False
    assert supervisor["stake_growth_authorized"] is False
    assert supervisor["v3_strategy_mutation_authorized"] is False
    assert supervisor["v4_strategy_mutation_authorized"] is False
    assert supervisor["broad_autonomous_live_rollout_authorized"] is False
    assert supervisor["third_order_authorized"] is False
    assert supervisor["official_reconciliation_required_after_success"] is True

    bindings = {
        "installer_git_blob_sha": ROOT / "ops" / "phase15_submission_supervisor" / "install_macos.sh",
        "supervisor_git_blob_sha": SUPERVISOR,
        "reconcile_helper_git_blob_sha": RECONCILE,
        "start_helper_git_blob_sha": START,
        "prepare_runner_git_blob_sha": ROOT / "scripts" / "run_phase15_v3_canary_prepare_watch.py",
        "prepare_service_unit_git_blob_sha": ROOT / "deploy" / "bp-phase15-canary-prepare-watch.service",
        "canary_git_blob_sha": ROOT / "src" / "bp_engine" / "execution" / "canary.py",
        "live_git_blob_sha": ROOT / "src" / "bp_engine" / "execution" / "live.py",
        "arm_helper_git_blob_sha": ROOT / "scripts" / "deploy" / "phase15_v3_canary_arm_cloudshell.sh",
        "executor_git_blob_sha": ROOT / "scripts" / "deploy" / "phase15_v3_canary_executor.py",
        "telegram_approval_contract_git_blob_sha": ROOT / "src" / "bp_engine" / "execution" / "telegram_approval.py",
    }
    for field, path in bindings.items():
        actual = subprocess.run(
            ["git", "hash-object", str(path)],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert supervisor[field] == actual


def test_supervisor_and_reconcile_helper_have_no_direct_order_path() -> None:
    supervisor_text = SUPERVISOR.read_text(encoding="utf-8")
    reconcile_text = RECONCILE.read_text(encoding="utf-8")

    ast.parse(supervisor_text)

    for forbidden in (
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_WALLET_ADDRESS",
        "BP_TELEGRAM_BOT_TOKEN",
        "create_limit_order",
        "post_order",
        "cancel_order",
        "PHASE15_ACCEPT_REAL_MONEY",
    ):
        assert forbidden not in supervisor_text
        assert forbidden not in reconcile_text

    for marker in (
        "second-canary.attempt.json",
        "closed_before_submission",
        "controlled_supervisor_candidate_expired_without_network_attempt",
        "second_canary_network_attempt_marker_present",
        "second_canary_network_attempt_marker_appeared_before_reconciliation",
        "PHASE15_CONTROLLED_CANARY_RECONCILIATION=PASS",
    ):
        assert marker in reconcile_text

    completed = subprocess.run(
        ["bash", "-n", str(RECONCILE)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_prepare_start_allows_only_source_truth_bound_supervisor_restarts() -> None:
    text = START.read_text(encoding="utf-8")
    for marker in (
        "supervisor_start_authorized",
        'supervisor.get("authorized") is True',
        'supervisor.get("completed") is False',
        'controlled.get("consumed") is False',
        'controlled.get("network_attempt_observed") is False',
        'supervisor.get("max_network_submission_attempts") == 1',
        'supervisor.get("target_notional_usd") == 5',
        'supervisor.get("start_helper_git_blob_sha") == sys.argv[2]',
        'supervisor.get("executor_git_blob_sha") == sys.argv[8]',
        'watch["start_authorized"] is True or supervisor_start_authorized',
    ):
        assert marker in text

    completed = subprocess.run(
        ["bash", "-n", str(START)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_authorization_evidence_matches_source_truth() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    supervisor = state["phase_15_v3_live_canary"]["controlled_submission_supervisor"]
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))

    assert evidence["limits"]["target_notional_usd"] == 5
    assert evidence["limits"]["max_network_submission_attempts"] == 1
    assert evidence["limits"]["additional_network_attempts_authorized"] is False
    assert evidence["fail_closed"]["stop_on_network_attempt_without_clear_success"] is True
    assert evidence["fail_closed"]["stop_on_ambiguous_network_attempt"] is True
    assert evidence["recycle_policy"]["allowed_only_before_network_attempt"] is True
    assert evidence["recycle_policy"]["market_end_grace_seconds"] == 20

    for field, value in evidence["bindings"].items():
        assert supervisor[field] == value
