from __future__ import annotations

import importlib.util
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bp_engine.execution.fast_live import verify_source_authorization

ROOT = Path(__file__).resolve().parents[2]
DEACTIVATE = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase15_v3_telegram_auto_approver_deactivate_operator.sh"
)
CANDIDATE = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase15_v3_fast_live_build_authorization_candidate.py"
)


def _candidate_module():
    spec = importlib.util.spec_from_file_location(
        "phase15_fast_live_build_authorization_candidate",
        CANDIDATE,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _state() -> dict[str, object]:
    return {
        "live_trading_enabled": False,
        "phase_15_v3_live_canary": {
            "live_trading_enabled": False,
            "status": "SECOND_LIVE_CANARY_RECONCILED_ZERO_FILL",
            "operator_telegram_auto_approver": {
                "status": "ACTIVE_LIVE_AUTO_APPROVE",
                "live_auto_approve_authorized": True,
                "service_state": "running",
                "stake_growth_authorized": False,
                "v3_strategy_mutation_authorized": False,
                "v4_mutation_authorized": False,
            },
        },
    }


def _deactivation(main: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "purpose": "phase15-v3-telegram-auto-approver-deactivation-v1",
        "status": "DEACTIVATED_VERIFIED",
        "repository_main": main,
        "service_active_after": False,
        "service_enabled_or_loaded_after": False,
        "matching_process_present_after": False,
        "live_auto_approve_runtime_effective_after": False,
        "source_truth_live_auto_approve_authorized_after": True,
        "telegram_session_deleted": False,
        "telegram_credentials_mutated": False,
        "project_state_mutated": False,
        "live_trading_enabled": False,
        "real_order_submitted": False,
        "observed_at": "2026-09-29T15:30:00+00:00",
    }


def test_operator_auto_approver_deactivation_helper_is_shell_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(DEACTIVATE)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_operator_auto_approver_deactivation_is_explicit_and_scoped() -> None:
    text = DEACTIVATE.read_text(encoding="utf-8")

    for marker in (
        "I_ACCEPT_STOP_LEGACY_TELEGRAM_AUTO_APPROVER",
        "checkout_is_not_current_main",
        "working_tree_not_clean",
        "auto_approver_source_truth_not_active",
        "com.bp.telegram-auto-approver",
        "bp-telegram-auto-approver.service",
        "launchctl disable",
        "launchctl bootout",
        "launchctl print-disabled",
        "systemctl --user stop",
        "systemctl --user disable",
        "[b]p_telegram_auto_approver",
        "DEACTIVATED_VERIFIED",
        "phase15-v3-telegram-auto-approver-deactivation-v1",
        "service_active_after",
        "service_enabled_or_loaded_after",
        "matching_process_present_after",
        "live_auto_approve_runtime_effective_after",
        "source_truth_live_auto_approve_authorized_after",
        "telegram_session_deleted",
        "telegram_credentials_mutated",
        "project_state_mutated",
        "LIVE_TRADING_ENABLED=false",
        "REAL_ORDER_SUBMITTED=false",
    ):
        assert marker in text

    for forbidden in (
        "kill -9",
        "pkill ",
        "rm -f ~/.config/bp",
        "rm -rf ~/.config/bp",
        "rm -f ~/Library",
        "rm -rf ~/Library",
        "PROJECT_STATE.json >",
        "PROJECT_STATE.json.tmp",
        "gcloud ",
        "post_order",
    ):
        assert forbidden not in text


def test_candidate_disables_auto_approver_without_enabling_live_flags() -> None:
    module = _candidate_module()
    main = "a" * 40
    authorized_at = datetime(2026, 9, 29, 15, 30, tzinfo=UTC)
    source = _state()

    candidate = module.build_candidate(
        state=source,
        deactivation=_deactivation(main),
        expected_main=main,
        authorization_id="fast-live-continuous-session-test",
        expires_at=authorized_at + timedelta(hours=8),
        authorized_at=authorized_at,
        deactivation_evidence_reference=(
            "docs/evidence/phase-15-v3-telegram-auto-approver-"
            "deactivation-test.json"
        ),
    )

    assert source["phase_15_v3_live_canary"][
        "operator_telegram_auto_approver"
    ]["live_auto_approve_authorized"] is True

    phase = candidate["phase_15_v3_live_canary"]
    auto = phase["operator_telegram_auto_approver"]
    auth = phase["fast_live_preauthorization"]

    assert candidate["live_trading_enabled"] is False
    assert phase["live_trading_enabled"] is False
    assert auto["status"] == (
        "DEACTIVATED_FOR_MANUAL_TELEGRAM_CONTINUOUS_LIVE"
    )
    assert auto["live_auto_approve_authorized"] is False
    assert auto["service_state"] == "stopped"
    assert auth["status"] == "AUTHORIZED_CONTINUOUS_SESSION"
    assert auth["authorization_mode"] == "manual-telegram-continuous-v1"
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
    assert verified["authorization_id"] == (
        "fast-live-continuous-session-test"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("service_active_after", True),
        ("service_enabled_or_loaded_after", True),
        ("matching_process_present_after", True),
        ("live_auto_approve_runtime_effective_after", True),
        ("project_state_mutated", True),
        ("live_trading_enabled", True),
        ("real_order_submitted", True),
    ],
)
def test_candidate_rejects_unsafe_deactivation_evidence(
    field: str,
    value: bool,
) -> None:
    module = _candidate_module()
    main = "b" * 40
    evidence = _deactivation(main)
    evidence[field] = value

    with pytest.raises(module.CandidateError, match="deactivation evidence unsafe"):
        module.build_candidate(
            state=_state(),
            deactivation=evidence,
            expected_main=main,
            authorization_id="fast-live-continuous-session-test",
            expires_at=datetime(2026, 9, 30, tzinfo=UTC),
            authorized_at=datetime(2026, 9, 29, 15, 30, tzinfo=UTC),
            deactivation_evidence_reference="docs/evidence/deactivated.json",
        )


def test_candidate_generator_refuses_direct_source_truth_overwrite() -> None:
    text = CANDIDATE.read_text(encoding="utf-8")
    for marker in (
        "I_ACCEPT_GENERATE_REVIEWABLE_CONTINUOUS_LIVE_AUTHORIZATION_CANDIDATE",
        "candidate generator refuses to overwrite PROJECT_STATE",
        "deactivation evidence project-state hash mismatch",
        "deactivation evidence must be stored under the repository root",
        "verify_source_authorization",
        "requires_telegram_approval=True",
        "continuous_session=True",
        "GLOBAL_LIVE_TRADING_ENABLED=false",
        "PHASE_LIVE_TRADING_ENABLED=false",
        "RUNTIME_AUTHORIZATION_CREATED=false",
        "PRODUCTION_MUTATION_PERFORMED=false",
        "REAL_ORDER_SUBMITTED=false",
    ):
        assert marker in text
