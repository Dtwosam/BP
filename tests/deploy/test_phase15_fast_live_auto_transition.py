from __future__ import annotations

import importlib.util
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bp_engine.execution.fast_live import (
    FAST_LIVE_AUTO_APPROVAL_CONTRACT_BLOB_SHA,
    FAST_LIVE_CONTINUOUS_AUTO_AUTHORIZATION_MODE,
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
        "exact_candidate_prompt_not_accepted",
        "mutated_candidate_prompt_not_rejected",
        "auto_approver_plist_wrong_checkout",
        "AUTO_APPROVER_SERVICE_ACTIVE=true",
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
        "exact_candidate_prompt_not_prepared_for_auto_click",
        "candidate_approve_callback_changed",
        "launchctl kickstart -k",
        "auto_approver_pid_did_not_change",
        "auto_approver_process_not_stable_after_restart",
        "UPGRADED_VERIFIED",
        "live_auto_approve_runtime_effective_after",
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
    ):
        assert forbidden not in text


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
        FAST_LIVE_CONTINUOUS_AUTO_AUTHORIZATION_MODE
    )
    assert auth["requires_telegram_approval"] is True
    assert auth["max_network_submission_attempts_per_intent"] == 1
    assert auth["target_notional_usd"] == 5
    assert auth["max_trade_size_usd"] == 10
    assert auth["max_total_exposure_usd"] == 10
    assert auth["max_daily_loss_usd"] == 10
    assert auth["max_consecutive_losses"] == 1
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
        FAST_LIVE_CONTINUOUS_AUTO_AUTHORIZATION_MODE
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("service_active_after", False),
        ("live_auto_approve_runtime_effective_after", False),
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
