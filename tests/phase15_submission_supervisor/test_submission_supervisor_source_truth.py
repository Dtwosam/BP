from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

from bp_engine.execution.fast_live import (
    FAST_LIVE_AUTO_APPROVAL_CONTRACT_BLOB_SHA,
)

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
INSTALLER = ROOT / "ops" / "phase15_submission_supervisor" / "install_macos.sh"
ACTIVATION_EVIDENCE = (
    ROOT
    / "docs"
    / "evidence"
    / "phase-15-controlled-submission-supervisor-activation-pass-production-20260927.json"
)
RUNTIME_REPAIR_COMPLETION_EVIDENCE = (
    ROOT
    / "docs"
    / "evidence"
    / "phase-15-controlled-submission-supervisor-runtime-repair-completion-20260927.json"
)
RECOVERED_TRANSIENT_EVIDENCE = (
    ROOT
    / "docs"
    / "evidence"
    / "phase-15-controlled-submission-supervisor-recovered-transient-20260927.json"
)


def test_submission_supervisor_source_truth_is_narrow_and_bound() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    gate = state["phase_15_v3_live_canary"]
    second = gate["second_live_canary_authorization"]
    controlled = gate["controlled_auto_approved_canary_authorization"]
    supervisor = gate["controlled_submission_supervisor"]

    assert second["status"] == "CONSUMED_SUBMITTED_RECONCILIATION_REQUIRED"
    assert second["max_network_submission_attempts"] == 1
    assert controlled["consumed"] is True
    assert controlled["completion_condition"] == "real_five_dollar_submission_recorded"
    assert controlled["candidate_preparation_is_completion"] is False
    assert controlled["pre_network_candidate_recycling_authorized"] is True

    assert supervisor["status"] == "TERMINAL_REAL_SUBMISSION_SUCCEEDED"
    assert supervisor["authorized"] is True
    assert supervisor["completed"] is True
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
    assert supervisor["deployment_performed"] is True
    assert supervisor["activation_performed"] is True
    assert supervisor["activation_result"] == "PASS"
    assert supervisor["activated_from_main"] == (
        "0c146fa0dc502c1855c5636f89e363667a1f71f3"
    )
    assert supervisor["installation_authorization_consumed"] is True
    assert supervisor["launch_agent_label"] == "com.bp.phase15-submission-supervisor"
    assert supervisor["launch_agent_verified_running_at_install"] is True
    assert supervisor["waiting_for_real_submission"] is False
    assert supervisor["real_order_submission_observed"] is True
    assert supervisor["network_submission_attempt_observed"] is True
    assert supervisor["activation_evidence"] == (
        "docs/evidence/"
        "phase-15-controlled-submission-supervisor-activation-pass-production-20260927.json"
    )
    assert supervisor["runtime_health_status"] == "HEALTHY"
    assert supervisor["runtime_issue"] == "launchagent_gcloud_python_path_missing"
    assert supervisor["runtime_repair_authorized"] is True
    assert supervisor["runtime_repair_completed"] is True
    assert supervisor["runtime_repair_completed_at"] == "2026-09-27T20:30:28.267707+00:00"
    assert supervisor["runtime_repair_verified_healthy_through"] == (
        "2026-09-27T21:25:11.482332+00:00"
    )
    assert supervisor["runtime_issue_last_observed_at"] == (
        "2026-09-27T21:23:28.997342+00:00"
    )
    assert supervisor["runtime_transient_recurrence_observed"] is True
    assert supervisor["runtime_transient_recurrence_count_observed"] == 1
    assert supervisor["runtime_transient_recovered_at"] == (
        "2026-09-27T21:23:55.116475+00:00"
    )
    assert supervisor["runtime_transient_observation_evidence"] == (
        "docs/evidence/"
        "phase-15-controlled-submission-supervisor-recovered-transient-20260927.json"
    )
    assert supervisor["runtime_repair_completion_evidence"] == (
        "docs/evidence/"
        "phase-15-controlled-submission-supervisor-runtime-repair-completion-20260927.json"
    )
    assert supervisor["runtime_repair_additional_network_attempts_authorized"] is False
    assert supervisor["runtime_repair_live_scope_expansion_authorized"] is False

    bindings = {
        "installer_git_blob_sha": (
            ROOT / "ops" / "phase15_submission_supervisor" / "install_macos.sh"
        ),
        "supervisor_git_blob_sha": SUPERVISOR,
        "reconcile_helper_git_blob_sha": RECONCILE,
        "start_helper_git_blob_sha": START,
        "prepare_runner_git_blob_sha": (
            ROOT / "scripts" / "run_phase15_v3_canary_prepare_watch.py"
        ),
        "prepare_service_unit_git_blob_sha": (
            ROOT / "deploy" / "bp-phase15-canary-prepare-watch.service"
        ),
        "canary_git_blob_sha": ROOT / "src" / "bp_engine" / "execution" / "canary.py",
        "arm_helper_git_blob_sha": (
            ROOT / "scripts" / "deploy" / "phase15_v3_canary_arm_cloudshell.sh"
        ),
        "executor_git_blob_sha": (
            ROOT / "scripts" / "deploy" / "phase15_v3_canary_executor.py"
        ),
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

    current_telegram_approval = subprocess.run(
        [
            "git",
            "hash-object",
            str(
                ROOT
                / "src"
                / "bp_engine"
                / "execution"
                / "telegram_approval.py"
            ),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    auto = gate["operator_telegram_auto_approver"]
    assert supervisor["telegram_approval_contract_git_blob_sha"] == (
        "930b62514712bd40400550da3ea5bbed533198da"
    )
    assert auto["approval_contract_git_blob_sha"] == (
        FAST_LIVE_AUTO_APPROVAL_CONTRACT_BLOB_SHA
    )
    assert current_telegram_approval == (
        FAST_LIVE_AUTO_APPROVAL_CONTRACT_BLOB_SHA
    )
    assert current_telegram_approval == auto["approval_contract_git_blob_sha"]
    assert current_telegram_approval != (
        supervisor["telegram_approval_contract_git_blob_sha"]
    )

    assert supervisor["live_git_blob_sha"] == (
        "0617ffeda8365cdd6ab2636af00e58ca8954c4db"
    )
    current_live = subprocess.run(
        ["git", "hash-object", str(ROOT / "src" / "bp_engine" / "execution" / "live.py")],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    repair = gate["second_canary_db_reconciliation"]
    assert current_live == repair["live_account_snapshot_runtime_fix_git_blob_sha"]
    assert current_live != supervisor["live_git_blob_sha"]
    assert repair["live_account_snapshot_runtime_fix_deployed"] is True


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

    repaired_fields = {
        "supervisor_git_blob_sha",
        "installer_git_blob_sha",
        "canary_git_blob_sha",
    }
    for field, value in evidence["bindings"].items():
        if field in repaired_fields:
            continue
        assert supervisor[field] == value

    # Original authorization evidence is immutable history. Runtime repair
    # evidence, not the original authorization record, binds the repaired
    # supervisor/installer blobs.
    assert evidence["bindings"]["supervisor_git_blob_sha"] == (
        "8af3b517919fc3b4a400bbd69f90908fa89ecc92"
    )
    assert evidence["bindings"]["installer_git_blob_sha"] == (
        "0d0bc12cda308a1413a3e99b49ab3d67574ee276"
    )
    assert evidence["bindings"]["canary_git_blob_sha"] == (
        "df5e60b1b2ba632fd77d65103509fac70187be7d"
    )
    assert (
        supervisor["supervisor_git_blob_sha"]
        != evidence["bindings"]["supervisor_git_blob_sha"]
    )
    assert (
        supervisor["installer_git_blob_sha"]
        != evidence["bindings"]["installer_git_blob_sha"]
    )
    assert (
        supervisor["canary_git_blob_sha"]
        != evidence["bindings"]["canary_git_blob_sha"]
    )


def test_macos_installer_is_explicit_and_secret_free() -> None:
    text = INSTALLER.read_text(encoding="utf-8")
    for marker in (
        "PHASE15_ACCEPT_CONTROLLED_SUBMISSION_SUPERVISOR",
        "explicit_supervisor_install_authorization_required",
        "BP_TELEGRAM_AUTO_APPROVE=true",
        "com.bp.telegram-auto-approver",
        "installer_git_blob_sha",
        "AUTHORIZED_NOT_DEPLOYED",
        "MAX_NETWORK_SUBMISSION_ATTEMPTS=1",
        "TARGET_NOTIONAL_USD=5",
        "SuccessfulExit",
        "GCLOUD=$(command -v gcloud || true)",
        "PYTHON=$(command -v python3 || true)",
        "<string>--gcloud-bin</string>",
        "<string>--python-bin</string>",
        "DEGRADED_GCLOUD_NOT_FOUND",
        '"HEALTHY"',
        "runtime_repair_authorized",
        "PHASE15_CONTROLLED_SUBMISSION_SUPERVISOR_INSTALL=PASS",
    ):
        assert marker in text

    for forbidden in (
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_WALLET_ADDRESS",
        "BP_TELEGRAM_BOT_TOKEN",
        "PHASE15_ACCEPT_REAL_MONEY",
    ):
        assert forbidden not in text

    completed = subprocess.run(
        ["bash", "-n", str(INSTALLER)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_activation_evidence_matches_active_supervisor_state() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    supervisor = state["phase_15_v3_live_canary"]["controlled_submission_supervisor"]
    evidence = json.loads(ACTIVATION_EVIDENCE.read_text(encoding="utf-8"))

    assert evidence["installer_result"] == "PASS"
    assert evidence["source_main"] == supervisor["activated_from_main"]
    assert evidence["installer_output"]["supervisor_main"] == supervisor["activated_from_main"]
    assert evidence["installer_output"]["max_network_submission_attempts"] == 1
    assert evidence["installer_output"]["target_notional_usd"] == 5
    assert evidence["resulting_source_truth"]["status"] == "ACTIVE_WAITING_FOR_REAL_SUBMISSION"
    assert supervisor["status"] == "TERMINAL_REAL_SUBMISSION_SUCCEEDED"
    assert evidence["resulting_source_truth"]["deployment_performed"] is True
    assert evidence["resulting_source_truth"]["activation_performed"] is True
    assert evidence["resulting_source_truth"]["completed"] is False
    assert evidence["safety_preserved"]["additional_network_attempts_authorized"] is False
    assert evidence["safety_preserved"]["third_order_authorized"] is False


def test_supervisor_uses_explicit_gcloud_binary() -> None:
    text = SUPERVISOR.read_text(encoding="utf-8")
    assert "gcloud_bin: Path" in text
    assert 'parser.add_argument(' in text
    assert '"--gcloud-bin"' in text
    assert '"--python-bin"' in text
    assert "configured gcloud binary is not executable" in text
    assert "configured python binary is not executable" in text
    assert "str(config.gcloud_bin)" in text
    assert "str(config.python_bin)" in text
    assert "def _tool_env(config: Config)" in text
    assert "str(config.gcloud_bin.parent)" in text
    assert "str(config.python_bin.parent)" in text
    assert "def _helper_env(config: Config)" in text
    assert "return _tool_env(config)" in text
    assert "env = _helper_env(config)" in text
    assert '["gcloud", "compute"' not in text


def test_gcloud_python_path_repair_evidence_matches_source_truth() -> None:
    evidence_path = (
        ROOT
        / "docs"
        / "evidence"
        / (
            "phase-15-controlled-submission-supervisor-"
            "gcloud-python-path-repair-authorization-20260927.json"
        )
    )
    state = json.loads(STATE.read_text(encoding="utf-8"))
    supervisor = state["phase_15_v3_live_canary"]["controlled_submission_supervisor"]
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))

    assert evidence["source_main"] == "cea0bbfe93e4cec6b11ba620cfe5e5470157c22a"
    assert evidence["incident"]["runtime_health_status"] == "DEGRADED_GCLOUD_PYTHON_PATH"
    assert evidence["incident"]["runtime_issue"] == supervisor["runtime_issue"]
    assert evidence["authorized_repair"]["exact_gcloud_binary_path_required"] is True
    assert evidence["authorized_repair"]["exact_python_binary_path_required"] is True
    assert evidence["authorized_repair"]["propagated_to_child_helpers"] is True
    assert evidence["authorized_repair"]["additional_network_attempts_authorized"] is False
    assert evidence["bindings"]["supervisor_git_blob_sha"] == supervisor["supervisor_git_blob_sha"]
    assert evidence["bindings"]["installer_git_blob_sha"] == (
        "a9ec9346ab5ec38f355134ad0379f55c04416e7d"
    )
    assert evidence["bindings"]["installer_git_blob_sha"] != supervisor["installer_git_blob_sha"]


def test_runtime_repair_completion_evidence_matches_source_truth() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    supervisor = state["phase_15_v3_live_canary"]["controlled_submission_supervisor"]
    evidence = json.loads(
        RUNTIME_REPAIR_COMPLETION_EVIDENCE.read_text(encoding="utf-8")
    )

    assert evidence["repo_main_at_recording"] == (
        "ac6b7078d6470d9c9e46c72cfc86ad856664a79b"
    )
    assert evidence["repaired_supervisor_main"] == (
        "d95ef3b4e908aa059df178b73be196bc9bd83fbf"
    )
    observation = evidence["operator_observation"]
    assert observation["launch_agent_state"] == "running"
    assert observation["healthy_decision_count"] == 42
    assert observation["repeated_action"] == "wait"
    assert observation["repeated_reason"] == "watcher_running"
    assert observation["new_transient_error_after_restart_observed"] is False
    assert observation["first_healthy_decision_at"] == supervisor["runtime_repair_completed_at"]
    assert observation["verified_healthy_through"] == (
        "2026-09-27T20:40:01.941354+00:00"
    )
    assert observation["verified_healthy_through"] != (
        supervisor["runtime_repair_verified_healthy_through"]
    )
    assert evidence["repaired_issue"]["result"] == "PASS"
    assert evidence["safety_preserved"]["additional_network_attempts_authorized"] is False
    assert evidence["safety_preserved"]["production_database_mutation_performed"] is False
    assert evidence["safety_preserved"]["order_submission_performed_by_repair"] is False


def test_supervisor_recovered_transient_evidence_matches_source_truth() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    supervisor = state["phase_15_v3_live_canary"]["controlled_submission_supervisor"]
    evidence = json.loads(RECOVERED_TRANSIENT_EVIDENCE.read_text(encoding="utf-8"))

    assert evidence["source_main"] == "39e5233fea5f34564cdcdfd1925e634321c4b5e1"
    observation = evidence["operator_observation"]
    assert observation["launch_agent_state"] == "running"
    assert observation["launch_agent_pid"] == 83085
    assert observation["telegram_auto_approver_state"] == "running"
    assert observation["telegram_auto_approver_pid"] == 73154
    assert observation["supervisor_decision_count"] == 79
    assert observation["supervisor_transient_error_count"] == 1
    assert observation["repeated_action"] == "wait"
    assert observation["repeated_reason"] == "watcher_running"
    assert observation["intent_id"] is None
    assert observation["stderr_empty"] is True

    recovered = evidence["recovered_transient"]
    assert recovered["observed_at"] == supervisor["runtime_issue_last_observed_at"]
    assert recovered["next_healthy_decision_at"] == supervisor["runtime_transient_recovered_at"]
    assert recovered["subsequent_healthy_decisions_observed"] is True
    assert recovered["terminal_failure"] is False
    assert recovered["supervisor_restart_observed"] is False
    assert evidence["interpretation"]["runtime_health_status"] == (
        supervisor["runtime_health_status"]
    )
    assert evidence["safety_preserved"]["controlled_canary_authorization_consumed"] is False
    assert evidence["safety_preserved"]["network_submission_attempt_observed"] is False
    assert evidence["safety_preserved"]["real_order_submission_observed"] is False
    assert evidence["safety_preserved"]["additional_network_attempts_authorized"] is False
    assert evidence["safety_preserved"]["live_scope_expansion_authorized"] is False
    assert evidence["safety_preserved"]["strategy_mutation_authorized"] is False
    assert evidence["safety_preserved"]["production_mutation_performed"] is False
