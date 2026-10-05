from __future__ import annotations

import importlib.util
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bp_engine.execution.fast_live import (
    FAST_LIVE_AUTO_APPROVAL_CONTRACT_BLOB_SHA,
    FAST_LIVE_CONTINUOUS_AUTO_AUTHORIZATION_MODE_V2,
    verify_source_authorization,
)

ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase15_v3_fast_live_build_auto_authorization_candidate.py"
)
PREFLIGHT = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase15_v3_telegram_auto_approver_continuous_preflight_operator.sh"
)
UPGRADE = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase15_v3_telegram_auto_approver_upgrade_continuous_operator.sh"
)


def _candidate_module():
    spec = importlib.util.spec_from_file_location(
        "phase15_fast_live_build_auto_authorization_candidate",
        CANDIDATE,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _state() -> dict[str, object]:
    return {
        "source_of_truth_version": "0.14.180",
        "live_trading_enabled": False,
        "phase_15_v3_live_canary": {
            "live_trading_enabled": False,
            "status": "SECOND_LIVE_CANARY_RECONCILED_ZERO_FILL",
            "operator_telegram_auto_approver": {
                "status": "ACTIVE_LIVE_AUTO_APPROVE",
                "live_auto_approve_authorized": True,
                "approval_contract_git_blob_sha": (
                    "930b62514712bd40400550da3ea5bbed533198da"
                ),
                "service_state": "running",
                "stake_growth_authorized": False,
                "v3_strategy_mutation_authorized": False,
                "v4_mutation_authorized": False,
            },
        },
    }


def _upgrade(main: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "purpose": (
            "phase15-v3-telegram-auto-approver-"
            "continuous-contract-upgrade-v1"
        ),
        "status": "UPGRADED_VERIFIED",
        "repository_main": main,
        "project_state_sha256": "source-state-hash-is-checked-by-cli",
        "approval_contract_git_blob_sha": (
            FAST_LIVE_AUTO_APPROVAL_CONTRACT_BLOB_SHA
        ),
        "service_active_after": True,
        "live_auto_approve_runtime_effective_after": True,
        "process_live_auto_approve_environment_verified": True,
        "launcher_mode": "wrapper",
        "exact_candidate_prompt_auto_click_prepared_verified": True,
        "mutated_candidate_prompt_rejected_verified": True,
        "telegram_session_deleted": False,
        "telegram_credentials_replaced": False,
        "project_state_mutated": False,
        "live_trading_enabled": False,
        "real_order_submitted_by_upgrade": False,
        "observed_at": "2026-09-29T16:00:00+00:00",
    }


def test_auto_approver_continuous_preflight_is_read_only() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(PREFLIGHT)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    text = PREFLIGHT.read_text(encoding="utf-8")
    for marker in (
        "PHASE15_TELEGRAM_AUTO_APPROVER_CONTINUOUS_PREFLIGHT=PASS",
        "checkout_is_not_current_main",
        "BP_TELEGRAM_AUTO_APPROVE=true",
        "approval_contract_fast_live_pin_mismatch",
        "approval_source",
        "ast.parse",
        "exact_candidate_prompt_not_accepted",
        "mutated_candidate_prompt_not_rejected",
        "auto_approver_plist_wrong_checkout",
        'EXPECTED_PYTHONPATH_RELATIVE="ops/telegram_auto_approver:src"',
        "EXPECTED_PYTHONPATH_ABSOLUTE",
        'EXPECTED_WRAPPER="$HOME/.local/share/bp-telegram-auto-approver/run.sh"',
        "auto_approver_wrapper_content_mismatch",
        "auto_approver_runtime_not_live_enabled",
        "auto_approver_runtime_pythonpath_mismatch",
        "AUTO_APPROVER_LAUNCHER_MODE",
        "AUTO_APPROVER_SERVICE_ACTIVE=true",
        "AUTO_APPROVE_RUNTIME_ENVIRONMENT=true",
        "RESTART_REQUIRED_FOR_RUNNING_PROCESS_UPGRADE=true",
        "MUTATIONS_PERFORMED=false",
        "PROJECT_STATE_MUTATED=false",
        "LIVE_TRADING_ENABLED=false",
        "REAL_ORDER_SUBMITTED=false",
    ):
        assert marker in text
    for forbidden in (
        "launchctl kickstart",
        "launchctl bootout",
        "launchctl disable",
        "systemctl ",
        "gcloud ",
        "post_order",
        "KILL_SWITCH_REMOVED=true",
        "from bp_engine.execution",
        "eval ",
    ):
        assert forbidden not in text


def test_auto_approver_continuous_upgrade_is_explicit_and_scoped() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(UPGRADE)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    text = UPGRADE.read_text(encoding="utf-8")
    for marker in (
        "I_ACCEPT_RESTART_AUTO_APPROVER_WITH_CONTINUOUS_CANDIDATE_CONTRACT",
        "evidence_path_must_be_under_repo_docs_evidence",
        "BP_TELEGRAM_AUTO_APPROVE=true",
        'EXPECTED_PYTHONPATH_RELATIVE="ops/telegram_auto_approver:src"',
        "EXPECTED_PYTHONPATH_ABSOLUTE",
        'EXPECTED_WRAPPER="$HOME/.local/share/bp-telegram-auto-approver/run.sh"',
        "auto_approver_wrapper_content_mismatch",
        "auto_approver_runtime_not_live_enabled_before",
        "auto_approver_runtime_not_live_enabled_after",
        "auto_approver_runtime_pythonpath_mismatch_before",
        "auto_approver_runtime_pythonpath_mismatch_after",
        "PROCESS_LIVE_AUTO_APPROVE_ENVIRONMENT_VERIFIED=true",
        "approval_source",
        "ast.parse",
        "exact_candidate_prompt_not_prepared_for_auto_click",
        "candidate_approve_callback_changed",
        "launchctl kickstart -k",
        "auto_approver_pid_did_not_change",
        "auto_approver_process_not_stable_after_restart",
        "UPGRADED_VERIFIED",
        "live_auto_approve_runtime_effective_after",
        "process_live_auto_approve_environment_verified",
        '"launcher_mode": os.environ.get',
        "exact_candidate_prompt_auto_click_prepared_verified",
        "mutated_candidate_prompt_rejected_verified",
        "project_state_mutated",
        "real_order_submitted_by_upgrade",
        "recorder_or_executor_mutated",
    ):
        assert marker in text
    for forbidden in (
        "launchctl disable",
        "launchctl bootout",
        "systemctl ",
        "gcloud ",
        "post_order",
        "executor.sh",
        "rm -f $HOME/.config/bp",
        "rm -rf $HOME/.config/bp",
        "from bp_engine.execution",
        "eval ",
    ):
        assert forbidden not in text


def test_auto_candidate_import_does_not_require_sqlalchemy() -> None:
    code = f"""
import builtins
import runpy
import sys

sys.path.insert(0, {str(ROOT / "src")!r})
real_import = builtins.__import__


def blocked_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name == "sqlalchemy" or name.startswith("sqlalchemy."):
        raise ModuleNotFoundError("sqlalchemy intentionally blocked")
    return real_import(name, globals, locals, fromlist, level)


builtins.__import__ = blocked_import
runpy.run_path({str(CANDIDATE)!r}, run_name="phase15_candidate_import_test")
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_auto_candidate_preserves_auto_approval_and_live_flags() -> None:
    module = _candidate_module()
    main = "d" * 40
    authorized_at = datetime(2026, 9, 29, 16, 5, tzinfo=UTC)
    source = _state()

    candidate = module.build_candidate(
        state=source,
        upgrade_evidence=_upgrade(main),
        expected_main=main,
        authorization_id="fast-live-auto-continuous-test",
        source_of_truth_version="0.14.181",
        expires_at=authorized_at + timedelta(hours=8),
        authorized_at=authorized_at,
        upgrade_evidence_reference=(
            "docs/evidence/phase-15-auto-approver-"
            "continuous-upgrade-test.json"
        ),
    )

    assert source["phase_15_v3_live_canary"][
        "operator_telegram_auto_approver"
    ]["approval_contract_git_blob_sha"] != (
        FAST_LIVE_AUTO_APPROVAL_CONTRACT_BLOB_SHA
    )

    phase = candidate["phase_15_v3_live_canary"]
    auto = phase["operator_telegram_auto_approver"]
    auth = phase["fast_live_preauthorization"]

    assert candidate["source_of_truth_version"] == "0.14.181"
    assert candidate["live_trading_enabled"] is False
    assert phase["live_trading_enabled"] is False
    assert auto["status"] == "ACTIVE_LIVE_AUTO_APPROVE"
    assert auto["live_auto_approve_authorized"] is True
    assert auto["continuous_candidate_prompt_authorized"] is True
    assert auto["approval_contract_git_blob_sha"] == (
        FAST_LIVE_AUTO_APPROVAL_CONTRACT_BLOB_SHA
    )
    assert auth["status"] == "AUTHORIZED_CONTINUOUS_SESSION"
    assert auth["authorization_mode"] == (
        FAST_LIVE_CONTINUOUS_AUTO_AUTHORIZATION_MODE_V2
    )
    assert auth["requires_telegram_approval"] is True
    assert auth["max_network_submission_attempts_per_intent"] == 1
    assert auth["target_notional_usd"] == 5
    assert auth["max_trade_size_usd"] == 10
    assert auth["max_total_exposure_usd"] == 10
    assert auth["max_daily_loss_usd"] == 10
    assert auth["max_consecutive_losses"] == 0
    assert auth["min_edge"] == 0.075
    assert auth["max_transit_seconds"] == 2
    assert auth["deployment_performed"] is False
    assert auth["activation_performed"] is False
    assert auth["kill_switch_removed"] is False
    assert auth["runtime_authorization_created"] is False
    assert auth["real_order_submitted"] is False

    verified = verify_source_authorization(
        candidate,
        expected_main=main,
        observed_at=authorized_at,
        requires_telegram_approval=True,
        continuous_session=True,
    )
    assert verified["authorization_id"] == "fast-live-auto-continuous-test"
    assert verified["authorization_mode"] == (
        FAST_LIVE_CONTINUOUS_AUTO_AUTHORIZATION_MODE_V2
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("service_active_after", False),
        ("live_auto_approve_runtime_effective_after", False),
        ("process_live_auto_approve_environment_verified", False),
        ("exact_candidate_prompt_auto_click_prepared_verified", False),
        ("mutated_candidate_prompt_rejected_verified", False),
        ("telegram_session_deleted", True),
        ("telegram_credentials_replaced", True),
        ("project_state_mutated", True),
        ("live_trading_enabled", True),
        ("real_order_submitted_by_upgrade", True),
    ],
)
def test_auto_candidate_rejects_unsafe_upgrade_evidence(
    field: str,
    value: bool,
) -> None:
    module = _candidate_module()
    main = "e" * 40
    evidence = _upgrade(main)
    evidence[field] = value

    with pytest.raises(module.CandidateError, match="upgrade evidence unsafe"):
        module.build_candidate(
            state=_state(),
            upgrade_evidence=evidence,
            expected_main=main,
            authorization_id="fast-live-auto-continuous-test",
            source_of_truth_version="0.14.181",
            expires_at=datetime(2026, 9, 30, tzinfo=UTC),
            authorized_at=datetime(2026, 9, 29, 16, 5, tzinfo=UTC),
            upgrade_evidence_reference="docs/evidence/upgrade.json",
        )


def test_auto_candidate_rejects_wrong_contract_and_inactive_source() -> None:
    module = _candidate_module()
    main = "f" * 40
    evidence = _upgrade(main)
    evidence["approval_contract_git_blob_sha"] = "0" * 40

    with pytest.raises(module.CandidateError, match="approval contract mismatch"):
        module.build_candidate(
            state=_state(),
            upgrade_evidence=evidence,
            expected_main=main,
            authorization_id="fast-live-auto-continuous-test",
            source_of_truth_version="0.14.181",
            expires_at=datetime(2026, 9, 30, tzinfo=UTC),
            authorized_at=datetime(2026, 9, 29, 16, 5, tzinfo=UTC),
            upgrade_evidence_reference="docs/evidence/upgrade.json",
        )

    inactive = _state()
    phase = inactive["phase_15_v3_live_canary"]
    assert isinstance(phase, dict)
    auto = phase["operator_telegram_auto_approver"]
    assert isinstance(auto, dict)
    auto["live_auto_approve_authorized"] = False

    with pytest.raises(
        module.CandidateError,
        match="must remain active and authorized",
    ):
        module.build_candidate(
            state=inactive,
            upgrade_evidence=_upgrade(main),
            expected_main=main,
            authorization_id="fast-live-auto-continuous-test",
            source_of_truth_version="0.14.181",
            expires_at=datetime(2026, 9, 30, tzinfo=UTC),
            authorized_at=datetime(2026, 9, 29, 16, 5, tzinfo=UTC),
            upgrade_evidence_reference="docs/evidence/upgrade.json",
        )


def test_auto_candidate_generator_is_non_deploying_and_non_overwriting() -> None:
    text = CANDIDATE.read_text(encoding="utf-8")
    for marker in (
        "I_ACCEPT_GENERATE_REVIEWABLE_CONTINUOUS_AUTO_LIVE_AUTHORIZATION_CANDIDATE",
        "candidate generator refuses to overwrite PROJECT_STATE",
        "upgrade evidence project-state hash mismatch",
        "upgrade evidence must be stored under the repository root",
        "--renew-existing-unactivated",
        "renew_existing_unactivated",
        "RENEWAL_OF_EXISTING_UNACTIVATED_AUTHORIZATION",
        "--replace-expired-cleaned-zero-attempt-session",
        "replace_expired_cleaned_zero_attempt_session",
        "REPLACEMENT_OF_EXPIRED_CLEANED_ZERO_ATTEMPT_SESSION",
        "zero-attempt cleanup evidence",
        "verify_source_authorization",
        "requires_telegram_approval=True",
        "continuous_session=True",
        "AUTO_APPROVAL_REMAINS_AUTHORIZED=true",
        "GLOBAL_LIVE_TRADING_ENABLED=false",
        "PHASE_LIVE_TRADING_ENABLED=false",
        "RUNTIME_AUTHORIZATION_CREATED=false",
        "PRODUCTION_MUTATION_PERFORMED=false",
        "REAL_ORDER_SUBMITTED=false",
    ):
        assert marker in text


def _renewable_state(
    module,
    *,
    upgrade_main: str,
    evidence_reference: str,
    authorized_at: datetime,
) -> tuple[dict[str, object], dict[str, object]]:
    evidence = _upgrade(upgrade_main)
    state = module.build_candidate(
        state=_state(),
        upgrade_evidence=evidence,
        expected_main=upgrade_main,
        authorization_id="fast-live-auto-continuous-old",
        source_of_truth_version="0.14.181",
        expires_at=authorized_at + timedelta(hours=8),
        authorized_at=authorized_at,
        upgrade_evidence_reference=evidence_reference,
    )
    return state, evidence


def test_auto_candidate_can_renew_only_existing_unactivated_authorization() -> None:
    module = _candidate_module()
    upgrade_main = "1" * 40
    current_main = "2" * 40
    evidence_reference = "docs/evidence/upgrade.json"
    first_authorized_at = datetime(2026, 9, 29, 16, 5, tzinfo=UTC)
    renewal_authorized_at = first_authorized_at + timedelta(hours=1)
    source, evidence = _renewable_state(
        module,
        upgrade_main=upgrade_main,
        evidence_reference=evidence_reference,
        authorized_at=first_authorized_at,
    )

    source_phase = source["phase_15_v3_live_canary"]
    assert isinstance(source_phase, dict)
    source_auto = source_phase["operator_telegram_auto_approver"]
    assert isinstance(source_auto, dict)
    original_upgrade_main = source_auto["continuous_contract_upgrade_main"]
    original_upgraded_at = source_auto["continuous_contract_upgraded_at"]

    candidate = module.build_candidate(
        state=source,
        upgrade_evidence=evidence,
        expected_main=current_main,
        authorization_id="fast-live-auto-continuous-renewed",
        source_of_truth_version="0.14.182",
        expires_at=renewal_authorized_at + timedelta(hours=12),
        authorized_at=renewal_authorized_at,
        upgrade_evidence_reference=evidence_reference,
        renew_existing_unactivated=True,
    )

    phase = candidate["phase_15_v3_live_canary"]
    assert isinstance(phase, dict)
    auto = phase["operator_telegram_auto_approver"]
    auth = phase["fast_live_preauthorization"]
    assert isinstance(auto, dict)
    assert isinstance(auth, dict)

    assert candidate["source_of_truth_version"] == "0.14.182"
    assert candidate["live_trading_enabled"] is False
    assert phase["live_trading_enabled"] is False
    assert auto["continuous_contract_upgrade_main"] == original_upgrade_main
    assert auto["continuous_contract_upgraded_at"] == original_upgraded_at
    assert auth["authorization_id"] == "fast-live-auto-continuous-renewed"
    assert auth["authorized_at_main"] == current_main
    assert auth["authorization_mode"] == FAST_LIVE_CONTINUOUS_AUTO_AUTHORIZATION_MODE_V2
    assert auth["target_notional_usd"] == 5
    assert auth["max_trade_size_usd"] == 10
    assert auth["max_total_exposure_usd"] == 10
    assert auth["max_daily_loss_usd"] == 10
    assert auth["max_consecutive_losses"] == 0
    assert auth["min_edge"] == 0.075
    assert auth["max_transit_seconds"] == 2
    assert auth["requires_telegram_approval"] is True
    assert auth["max_network_submission_attempts_per_intent"] == 1
    assert auth["deployment_performed"] is False
    assert auth["activation_performed"] is False
    assert auth["runtime_authorization_created"] is False
    assert auth["kill_switch_removed"] is False
    assert auth["real_order_submitted"] is False


def test_auto_candidate_can_renew_expired_unactivated_authorization() -> None:
    module = _candidate_module()
    upgrade_main = "9" * 40
    current_main = "a" * 40
    evidence_reference = "docs/evidence/upgrade.json"
    first_authorized_at = datetime(2026, 9, 29, 16, 5, tzinfo=UTC)
    renewal_authorized_at = first_authorized_at + timedelta(hours=9)
    source, evidence = _renewable_state(
        module,
        upgrade_main=upgrade_main,
        evidence_reference=evidence_reference,
        authorized_at=first_authorized_at,
    )

    phase = source["phase_15_v3_live_canary"]
    assert isinstance(phase, dict)
    old_auth = phase["fast_live_preauthorization"]
    assert isinstance(old_auth, dict)
    assert datetime.fromisoformat(str(old_auth["expires_at"])) < renewal_authorized_at

    candidate = module.build_candidate(
        state=source,
        upgrade_evidence=evidence,
        expected_main=current_main,
        authorization_id="fast-live-auto-continuous-renewed-after-expiry",
        source_of_truth_version="0.14.182",
        expires_at=renewal_authorized_at + timedelta(hours=12),
        authorized_at=renewal_authorized_at,
        upgrade_evidence_reference=evidence_reference,
        renew_existing_unactivated=True,
    )

    renewed = candidate["phase_15_v3_live_canary"]["fast_live_preauthorization"]
    assert renewed["authorization_id"] == (
        "fast-live-auto-continuous-renewed-after-expiry"
    )
    assert renewed["authorized_at_main"] == current_main
    assert renewed["deployment_performed"] is False
    assert renewed["activation_performed"] is False
    assert renewed["runtime_authorization_created"] is False
    assert renewed["kill_switch_removed"] is False
    assert renewed["real_order_submitted"] is False

    verified = verify_source_authorization(
        candidate,
        expected_main=current_main,
        observed_at=renewal_authorized_at,
        requires_telegram_approval=True,
        continuous_session=True,
    )
    assert verified["authorization_id"] == (
        "fast-live-auto-continuous-renewed-after-expiry"
    )


@pytest.mark.parametrize(
    "field",
    [
        "deployment_performed",
        "activation_performed",
        "runtime_authorization_created",
        "kill_switch_removed",
        "real_order_submitted",
    ],
)
def test_auto_candidate_renewal_rejects_consumed_or_mutated_session(
    field: str,
) -> None:
    module = _candidate_module()
    upgrade_main = "3" * 40
    current_main = "4" * 40
    evidence_reference = "docs/evidence/upgrade.json"
    authorized_at = datetime(2026, 9, 29, 16, 5, tzinfo=UTC)
    source, evidence = _renewable_state(
        module,
        upgrade_main=upgrade_main,
        evidence_reference=evidence_reference,
        authorized_at=authorized_at,
    )
    phase = source["phase_15_v3_live_canary"]
    assert isinstance(phase, dict)
    auth = phase["fast_live_preauthorization"]
    assert isinstance(auth, dict)
    auth[field] = True

    with pytest.raises(module.CandidateError, match="is not renewable"):
        module.build_candidate(
            state=source,
            upgrade_evidence=evidence,
            expected_main=current_main,
            authorization_id="fast-live-auto-continuous-renewed",
            source_of_truth_version="0.14.182",
            expires_at=authorized_at + timedelta(hours=12),
            authorized_at=authorized_at + timedelta(minutes=1),
            upgrade_evidence_reference=evidence_reference,
            renew_existing_unactivated=True,
        )


def test_auto_candidate_renewal_revalidates_existing_risk_contract() -> None:
    module = _candidate_module()
    upgrade_main = "5" * 40
    current_main = "6" * 40
    evidence_reference = "docs/evidence/upgrade.json"
    authorized_at = datetime(2026, 9, 29, 16, 5, tzinfo=UTC)
    source, evidence = _renewable_state(
        module,
        upgrade_main=upgrade_main,
        evidence_reference=evidence_reference,
        authorized_at=authorized_at,
    )
    phase = source["phase_15_v3_live_canary"]
    assert isinstance(phase, dict)
    auth = phase["fast_live_preauthorization"]
    assert isinstance(auth, dict)
    auth["max_daily_loss_usd"] = 11

    with pytest.raises(Exception, match="max_daily_loss_usd"):
        module.build_candidate(
            state=source,
            upgrade_evidence=evidence,
            expected_main=current_main,
            authorization_id="fast-live-auto-continuous-renewed",
            source_of_truth_version="0.14.182",
            expires_at=authorized_at + timedelta(hours=12),
            authorized_at=authorized_at + timedelta(minutes=1),
            upgrade_evidence_reference=evidence_reference,
            renew_existing_unactivated=True,
        )


def test_auto_candidate_renewal_requires_exact_existing_upgrade_evidence() -> None:
    module = _candidate_module()
    upgrade_main = "7" * 40
    current_main = "8" * 40
    evidence_reference = "docs/evidence/upgrade.json"
    authorized_at = datetime(2026, 9, 29, 16, 5, tzinfo=UTC)
    source, evidence = _renewable_state(
        module,
        upgrade_main=upgrade_main,
        evidence_reference=evidence_reference,
        authorized_at=authorized_at,
    )

    with pytest.raises(
        module.CandidateError,
        match="renewal upgrade evidence reference mismatch",
    ):
        module.build_candidate(
            state=source,
            upgrade_evidence=evidence,
            expected_main=current_main,
            authorization_id="fast-live-auto-continuous-renewed",
            source_of_truth_version="0.14.182",
            expires_at=authorized_at + timedelta(hours=12),
            authorized_at=authorized_at + timedelta(minutes=1),
            upgrade_evidence_reference="docs/evidence/different.json",
            renew_existing_unactivated=True,
        )


def _completed_cleanup_evidence(
    *,
    existing: dict[str, object],
    staged_release_main: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "purpose": "phase15-v3-fast-live-completed-session-cleanup-v1",
        "status": "CLEANUP_VERIFIED",
        "authorization_id": existing["authorization_id"],
        "authorization_mode": existing["authorization_mode"],
        "session_release_main": existing["authorized_at_main"],
        "runtime_expires_at": existing["expires_at"],
        "staged_release_main": staged_release_main,
        "cleanup_mode": "expired",
        "cleanup_completed": True,
        "prior_real_order_submitted": True,
        "kill_switch_engaged": True,
        "historical_state_preserved": True,
        "session_runtime_files_present": False,
        "session_pubsub_resources_present": False,
        "recorder_source_active": False,
        "executor_receiver_active": False,
        "recorder_runtime_authorization_present": False,
        "executor_runtime_authorization_present": False,
        "recorder_transport_key_present": False,
        "executor_transport_key_present": False,
        "executor_account_clean": True,
        "executor_open_order_count": 0,
        "executor_geo_country": "ZA",
        "executor_geo_blocked": False,
    }


def _zero_attempt_cleanup_evidence(
    *,
    existing: dict[str, object],
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "purpose": "phase15-v3-fast-live-expired-zero-attempt-cleanup-v1",
        "status": "CLEANUP_VERIFIED",
        "authorization_id": existing["authorization_id"],
        "authorization_mode": existing["authorization_mode"],
        "session_release_main": existing["authorized_at_main"],
        "runtime_expires_at": existing["expires_at"],
        "cleanup_mode": "expired_zero_attempt",
        "cleanup_completed": True,
        "prior_real_order_submitted": False,
        "zero_network_attempt_verified": True,
        "session_publication_count": 0,
        "session_network_submission_attempt_count": 0,
        "session_execution_result_count": 0,
        "session_real_order_submitted": False,
        "kill_switch_engaged": True,
        "historical_state_preserved": True,
        "session_runtime_files_present": False,
        "session_pubsub_resources_present": False,
        "recorder_source_active": False,
        "executor_receiver_active": False,
        "recorder_runtime_authorization_present": False,
        "executor_runtime_authorization_present": False,
        "recorder_transport_key_present": False,
        "executor_transport_key_present": False,
        "executor_account_clean": True,
        "executor_open_order_count": 0,
        "executor_geo_country": "ZA",
        "executor_geo_blocked": False,
    }


def test_auto_candidate_can_replace_expired_cleaned_zero_attempt_session() -> None:
    module = _candidate_module()
    upgrade_main = "1" * 40
    current_main = "2" * 40
    evidence_reference = "docs/evidence/upgrade.json"
    cleanup_reference = "docs/evidence/zero-attempt-cleanup.json"
    first_authorized_at = datetime(2026, 10, 1, 20, 10, tzinfo=UTC)
    replacement_authorized_at = first_authorized_at + timedelta(hours=13)
    source, evidence = _renewable_state(
        module,
        upgrade_main=upgrade_main,
        evidence_reference=evidence_reference,
        authorized_at=first_authorized_at,
    )
    phase = source["phase_15_v3_live_canary"]
    assert isinstance(phase, dict)
    old_auth = phase["fast_live_preauthorization"]
    assert isinstance(old_auth, dict)
    old_auth["deployment_performed"] = True
    old_auth["activation_performed"] = True
    old_auth["runtime_authorization_created"] = True
    old_auth["kill_switch_removed"] = True
    old_auth["real_order_submitted"] = False

    cleanup = _zero_attempt_cleanup_evidence(existing=old_auth)

    candidate = module.build_candidate(
        state=source,
        upgrade_evidence=evidence,
        expected_main=current_main,
        authorization_id="fast-live-auto-continuous-after-zero-attempt-cleanup",
        source_of_truth_version="0.14.182",
        expires_at=replacement_authorized_at + timedelta(hours=12),
        authorized_at=replacement_authorized_at,
        upgrade_evidence_reference=evidence_reference,
        replace_expired_cleaned_zero_attempt_session=True,
        zero_attempt_cleanup_evidence=cleanup,
        zero_attempt_cleanup_evidence_reference=cleanup_reference,
    )

    renewed = candidate["phase_15_v3_live_canary"]["fast_live_preauthorization"]
    assert renewed["authorization_id"] == (
        "fast-live-auto-continuous-after-zero-attempt-cleanup"
    )
    assert renewed["authorized_at_main"] == current_main
    assert renewed["deployment_performed"] is False
    assert renewed["activation_performed"] is False
    assert renewed["runtime_authorization_created"] is False
    assert renewed["kill_switch_removed"] is False
    assert renewed["real_order_submitted"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("cleanup_completed", False),
        ("zero_network_attempt_verified", False),
        ("prior_real_order_submitted", True),
        ("session_real_order_submitted", True),
        ("session_network_submission_attempt_count", 1),
        ("session_execution_result_count", 1),
        ("session_publication_count", -1),
        ("kill_switch_engaged", False),
        ("historical_state_preserved", False),
        ("session_runtime_files_present", True),
        ("session_pubsub_resources_present", True),
        ("recorder_source_active", True),
        ("executor_receiver_active", True),
        ("recorder_runtime_authorization_present", True),
        ("executor_runtime_authorization_present", True),
        ("recorder_transport_key_present", True),
        ("executor_transport_key_present", True),
        ("executor_account_clean", False),
        ("executor_geo_blocked", True),
        ("executor_open_order_count", 1),
    ],
)
def test_zero_attempt_replacement_rejects_unsafe_cleanup_evidence(
    field: str,
    value: object,
) -> None:
    module = _candidate_module()
    upgrade_main = "3" * 40
    current_main = "4" * 40
    evidence_reference = "docs/evidence/upgrade.json"
    authorized_at = datetime(2026, 10, 1, 20, 10, tzinfo=UTC)
    replacement_at = authorized_at + timedelta(hours=13)
    source, evidence = _renewable_state(
        module,
        upgrade_main=upgrade_main,
        evidence_reference=evidence_reference,
        authorized_at=authorized_at,
    )
    phase = source["phase_15_v3_live_canary"]
    assert isinstance(phase, dict)
    old_auth = phase["fast_live_preauthorization"]
    assert isinstance(old_auth, dict)
    old_auth["deployment_performed"] = True
    old_auth["activation_performed"] = True
    old_auth["runtime_authorization_created"] = True
    old_auth["kill_switch_removed"] = True
    cleanup = _zero_attempt_cleanup_evidence(existing=old_auth)
    cleanup[field] = value

    with pytest.raises(module.CandidateError, match="zero-attempt cleanup evidence unsafe"):
        module.build_candidate(
            state=source,
            upgrade_evidence=evidence,
            expected_main=current_main,
            authorization_id="fast-live-auto-continuous-after-zero-attempt-cleanup",
            source_of_truth_version="0.14.182",
            expires_at=replacement_at + timedelta(hours=12),
            authorized_at=replacement_at,
            upgrade_evidence_reference=evidence_reference,
            replace_expired_cleaned_zero_attempt_session=True,
            zero_attempt_cleanup_evidence=cleanup,
            zero_attempt_cleanup_evidence_reference=(
                "docs/evidence/zero-attempt-cleanup.json"
            ),
        )


def test_zero_attempt_replacement_requires_expired_prior_authorization() -> None:
    module = _candidate_module()
    upgrade_main = "5" * 40
    current_main = "6" * 40
    evidence_reference = "docs/evidence/upgrade.json"
    authorized_at = datetime(2026, 10, 1, 20, 10, tzinfo=UTC)
    source, evidence = _renewable_state(
        module,
        upgrade_main=upgrade_main,
        evidence_reference=evidence_reference,
        authorized_at=authorized_at,
    )
    phase = source["phase_15_v3_live_canary"]
    assert isinstance(phase, dict)
    old_auth = phase["fast_live_preauthorization"]
    assert isinstance(old_auth, dict)
    old_auth["deployment_performed"] = True
    old_auth["activation_performed"] = True
    old_auth["runtime_authorization_created"] = True
    old_auth["kill_switch_removed"] = True
    cleanup = _zero_attempt_cleanup_evidence(existing=old_auth)

    with pytest.raises(
        module.CandidateError,
        match="zero-attempt replacement requires expired authorization",
    ):
        module.build_candidate(
            state=source,
            upgrade_evidence=evidence,
            expected_main=current_main,
            authorization_id="fast-live-auto-continuous-too-early-zero-attempt",
            source_of_truth_version="0.14.182",
            expires_at=authorized_at + timedelta(hours=12),
            authorized_at=authorized_at + timedelta(hours=1),
            upgrade_evidence_reference=evidence_reference,
            replace_expired_cleaned_zero_attempt_session=True,
            zero_attempt_cleanup_evidence=cleanup,
            zero_attempt_cleanup_evidence_reference=(
                "docs/evidence/zero-attempt-cleanup.json"
            ),
        )


def test_auto_candidate_can_replace_expired_completed_cleaned_session() -> None:
    module = _candidate_module()
    upgrade_main = "a" * 40
    current_main = "b" * 40
    evidence_reference = "docs/evidence/upgrade.json"
    cleanup_reference = "docs/evidence/completed-cleanup.json"
    first_authorized_at = datetime(2026, 9, 29, 16, 5, tzinfo=UTC)
    replacement_authorized_at = first_authorized_at + timedelta(hours=9)
    source, evidence = _renewable_state(
        module,
        upgrade_main=upgrade_main,
        evidence_reference=evidence_reference,
        authorized_at=first_authorized_at,
    )
    old_auth = source["phase_15_v3_live_canary"]["fast_live_preauthorization"]
    assert isinstance(old_auth, dict)
    cleanup = _completed_cleanup_evidence(
        existing=old_auth,
        staged_release_main=current_main,
    )

    candidate = module.build_candidate(
        state=source,
        upgrade_evidence=evidence,
        expected_main=current_main,
        authorization_id="fast-live-auto-continuous-after-cleanup",
        source_of_truth_version="0.14.182",
        expires_at=replacement_authorized_at + timedelta(hours=12),
        authorized_at=replacement_authorized_at,
        upgrade_evidence_reference=evidence_reference,
        replace_completed_cleaned_session=True,
        completed_session_cleanup_evidence=cleanup,
        completed_session_cleanup_evidence_reference=cleanup_reference,
    )

    renewed = candidate["phase_15_v3_live_canary"]["fast_live_preauthorization"]
    assert renewed["authorization_id"] == "fast-live-auto-continuous-after-cleanup"
    assert renewed["authorized_at_main"] == current_main
    assert renewed["authorization_mode"] == FAST_LIVE_CONTINUOUS_AUTO_AUTHORIZATION_MODE_V2
    assert renewed["target_notional_usd"] == 5
    assert renewed["max_trade_size_usd"] == 10
    assert renewed["max_total_exposure_usd"] == 10
    assert renewed["max_daily_loss_usd"] == 10
    assert renewed["max_consecutive_losses"] == 0
    assert renewed["min_edge"] == 0.075
    assert renewed["requires_telegram_approval"] is True
    assert renewed["max_network_submission_attempts_per_intent"] == 1
    assert renewed["deployment_performed"] is False
    assert renewed["activation_performed"] is False
    assert renewed["runtime_authorization_created"] is False
    assert renewed["kill_switch_removed"] is False
    assert renewed["real_order_submitted"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("cleanup_completed", False),
        ("prior_real_order_submitted", False),
        ("kill_switch_engaged", False),
        ("historical_state_preserved", False),
        ("session_runtime_files_present", True),
        ("session_pubsub_resources_present", True),
        ("recorder_source_active", True),
        ("executor_receiver_active", True),
        ("recorder_runtime_authorization_present", True),
        ("executor_runtime_authorization_present", True),
        ("recorder_transport_key_present", True),
        ("executor_transport_key_present", True),
        ("executor_account_clean", False),
        ("executor_geo_blocked", True),
        ("executor_open_order_count", 1),
    ],
)
def test_completed_session_replacement_rejects_unsafe_cleanup_evidence(
    field: str,
    value: object,
) -> None:
    module = _candidate_module()
    upgrade_main = "c" * 40
    current_main = "d" * 40
    evidence_reference = "docs/evidence/upgrade.json"
    authorized_at = datetime(2026, 9, 29, 16, 5, tzinfo=UTC)
    replacement_at = authorized_at + timedelta(hours=9)
    source, evidence = _renewable_state(
        module,
        upgrade_main=upgrade_main,
        evidence_reference=evidence_reference,
        authorized_at=authorized_at,
    )
    old_auth = source["phase_15_v3_live_canary"]["fast_live_preauthorization"]
    assert isinstance(old_auth, dict)
    cleanup = _completed_cleanup_evidence(
        existing=old_auth,
        staged_release_main=current_main,
    )
    cleanup[field] = value

    with pytest.raises(module.CandidateError, match="completed-session cleanup evidence unsafe"):
        module.build_candidate(
            state=source,
            upgrade_evidence=evidence,
            expected_main=current_main,
            authorization_id="fast-live-auto-continuous-after-cleanup",
            source_of_truth_version="0.14.182",
            expires_at=replacement_at + timedelta(hours=12),
            authorized_at=replacement_at,
            upgrade_evidence_reference=evidence_reference,
            replace_completed_cleaned_session=True,
            completed_session_cleanup_evidence=cleanup,
            completed_session_cleanup_evidence_reference=(
                "docs/evidence/completed-cleanup.json"
            ),
        )


def test_completed_session_replacement_requires_expired_prior_authorization() -> None:
    module = _candidate_module()
    upgrade_main = "e" * 40
    current_main = "f" * 40
    evidence_reference = "docs/evidence/upgrade.json"
    authorized_at = datetime(2026, 9, 29, 16, 5, tzinfo=UTC)
    source, evidence = _renewable_state(
        module,
        upgrade_main=upgrade_main,
        evidence_reference=evidence_reference,
        authorized_at=authorized_at,
    )
    old_auth = source["phase_15_v3_live_canary"]["fast_live_preauthorization"]
    assert isinstance(old_auth, dict)
    cleanup = _completed_cleanup_evidence(
        existing=old_auth,
        staged_release_main=current_main,
    )

    with pytest.raises(
        module.CandidateError,
        match="completed-session replacement requires expired authorization",
    ):
        module.build_candidate(
            state=source,
            upgrade_evidence=evidence,
            expected_main=current_main,
            authorization_id="fast-live-auto-continuous-too-early",
            source_of_truth_version="0.14.182",
            expires_at=authorized_at + timedelta(hours=12),
            authorized_at=authorized_at + timedelta(hours=1),
            upgrade_evidence_reference=evidence_reference,
            replace_completed_cleaned_session=True,
            completed_session_cleanup_evidence=cleanup,
            completed_session_cleanup_evidence_reference=(
                "docs/evidence/completed-cleanup.json"
            ),
        )
