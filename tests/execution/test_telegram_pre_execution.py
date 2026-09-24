from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from bp_engine.execution.telegram_pre_execution import (
    PRE_EXECUTION_PURPOSE,
    PreExecutionError,
    evaluate_pre_execution_authorization,
    source_truth_sha256,
    verify_pre_execution_snapshot,
)

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "PROJECT_STATE.json"
MODULE = ROOT / "src" / "bp_engine" / "execution" / "telegram_pre_execution.py"


def _ready() -> dict[str, object]:
    return {
        "status": "execution_ready_origin_verified",
        "transport_key_id": "phase15-telegram-transport-v1",
        "origin_key_id": "phase15-telegram-origin-v1",
        "intent_id": "live-intent-pre-exec",
        "prediction_id": "prediction-pre-exec",
        "paper_order_id": "paper-pre-exec",
        "request_sha256": "1" * 64,
        "prepared_sha256": "2" * 64,
        "approval_sha256": "3" * 64,
        "approval_source_sha256": "4" * 64,
        "origin_attestation_sha256": "5" * 64,
        "origin_attested_at": "2026-09-24T21:00:02+00:00",
        "origin_expires_at": "2026-09-24T21:00:15+00:00",
        "retry_allowed": False,
        "network_action_performed": False,
        "executor_invoked": False,
        "real_order_submitted": False,
    }


def _authorized_state() -> dict[str, object]:
    return {
        "live_trading_enabled": False,
        "phase_15_v3_live_canary": {
            "live_trading_enabled": False,
            "phase15_canary_authorized": True,
            "canary_order_submitted": True,
            "pending_unsubmitted_intent": None,
            "v3_strategy_mutation_performed": False,
            "second_order_authorized": True,
            "automated_real_money_submission": True,
            "manual_real_money_submission_required": False,
            "telegram_one_tap_submission_authorized": True,
            "telegram_persistent_execution_transport_authorized": True,
            "telegram_pubsub_transport_authorized": True,
            "first_live_canary": {
                "official_reconciliation_complete": True,
            },
        },
    }


def test_current_source_truth_blocks_telegram_pre_execution() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    result = evaluate_pre_execution_authorization(
        ready_verification=_ready(),
        project_state=state,
    )

    assert result["status"] == "pre_execution_blocked"
    assert result["authorized"] is False
    assert result["purpose"] == PRE_EXECUTION_PURPOSE
    assert result["retry_allowed"] is False
    assert result["mutation_performed"] is False
    assert result["network_action_performed"] is False
    assert result["executor_invoked"] is False
    assert result["real_order_submitted"] is False
    for blocker in (
        "second_order_not_authorized",
        "automated_real_money_submission_not_authorized",
        "manual_submission_still_required",
        "telegram_one_tap_not_authorized",
        "persistent_execution_transport_not_authorized",
        "telegram_pubsub_transport_not_authorized",
    ):
        assert blocker in result["blockers"]


@pytest.mark.parametrize(
    ("path", "value", "blocker"),
    [
        (("live_trading_enabled",), True, "global_live_trading_not_safely_disabled"),
        (
            ("phase_15_v3_live_canary", "second_order_authorized"),
            False,
            "second_order_not_authorized",
        ),
        (
            ("phase_15_v3_live_canary", "automated_real_money_submission"),
            False,
            "automated_real_money_submission_not_authorized",
        ),
        (
            ("phase_15_v3_live_canary", "manual_real_money_submission_required"),
            True,
            "manual_submission_still_required",
        ),
        (
            ("phase_15_v3_live_canary", "telegram_one_tap_submission_authorized"),
            False,
            "telegram_one_tap_not_authorized",
        ),
        (
            (
                "phase_15_v3_live_canary",
                "telegram_persistent_execution_transport_authorized",
            ),
            False,
            "persistent_execution_transport_not_authorized",
        ),
        (
            ("phase_15_v3_live_canary", "telegram_pubsub_transport_authorized"),
            False,
            "telegram_pubsub_transport_not_authorized",
        ),
        (
            (
                "phase_15_v3_live_canary",
                "first_live_canary",
                "official_reconciliation_complete",
            ),
            False,
            "first_canary_reconciliation_not_complete",
        ),
    ],
)
def test_pre_execution_requires_every_explicit_gate(
    path: tuple[str, ...],
    value: object,
    blocker: str,
) -> None:
    state = _authorized_state()
    target: dict[str, object] = state
    for key in path[:-1]:
        nested = target[key]
        assert isinstance(nested, dict)
        target = nested
    target[path[-1]] = value

    result = evaluate_pre_execution_authorization(
        ready_verification=_ready(),
        project_state=state,
    )
    assert result["authorized"] is False
    assert blocker in result["blockers"]


def test_pre_execution_authorizes_only_fully_explicit_synthetic_state() -> None:
    state = _authorized_state()
    result = evaluate_pre_execution_authorization(
        ready_verification=_ready(),
        project_state=state,
    )

    assert result["status"] == "pre_execution_authorized"
    assert result["authorized"] is True
    assert result["blockers"] == []
    assert result["source_truth_sha256"] == source_truth_sha256(state)
    assert result["transport_key_id"] == "phase15-telegram-transport-v1"
    assert result["origin_key_id"] == "phase15-telegram-origin-v1"
    assert len(result["authorization_report_sha256"]) == 64
    assert result["mutation_performed"] is False
    assert result["executor_invoked"] is False
    assert result["real_order_submitted"] is False


def test_pre_execution_rejects_unverified_or_side_effecting_ready_state() -> None:
    for mutation in (
        {"status": "claimed_ready"},
        {"retry_allowed": True},
        {"executor_invoked": True},
        {"real_order_submitted": True},
    ):
        ready = _ready()
        ready.update(mutation)
        with pytest.raises(PreExecutionError):
            evaluate_pre_execution_authorization(
                ready_verification=ready,
                project_state=_authorized_state(),
            )


def test_source_truth_hash_changes_with_authorization_state() -> None:
    state = _authorized_state()
    changed = copy.deepcopy(state)
    phase = changed["phase_15_v3_live_canary"]
    assert isinstance(phase, dict)
    phase["second_order_authorized"] = False
    assert source_truth_sha256(state) != source_truth_sha256(changed)


def test_pre_execution_module_has_no_network_wallet_or_execution_path() -> None:
    text = MODULE.read_text(encoding="utf-8")
    compile(text, str(MODULE), "exec")
    for forbidden in (
        "httpx",
        "urllib",
        "requests",
        "subprocess",
        "gcloud",
        "google.cloud",
        "post_order",
        "create_limit_order",
        "cancel_order",
        "phase15_v3_canary_arm",
        "phase15_v3_canary_executor",
        "PHASE15_ACCEPT_REAL_MONEY",
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_WALLET_ADDRESS",
    ):
        assert forbidden not in text


def test_pre_execution_report_hash_changes_when_ready_binding_changes() -> None:
    state = _authorized_state()
    first = evaluate_pre_execution_authorization(
        ready_verification=_ready(),
        project_state=state,
    )
    changed_ready = _ready()
    changed_ready["request_sha256"] = "9" * 64
    second = evaluate_pre_execution_authorization(
        ready_verification=changed_ready,
        project_state=state,
    )
    assert first["authorization_report_sha256"] != second["authorization_report_sha256"]


def test_pre_execution_snapshot_rejects_source_truth_drift() -> None:
    state = _authorized_state()
    ready = _ready()
    snapshot = evaluate_pre_execution_authorization(
        ready_verification=ready,
        project_state=state,
    )

    changed = copy.deepcopy(state)
    phase = changed["phase_15_v3_live_canary"]
    assert isinstance(phase, dict)
    phase["second_order_authorized"] = False

    with pytest.raises(PreExecutionError, match="stale or modified"):
        verify_pre_execution_snapshot(
            snapshot,
            ready_verification=ready,
            project_state=changed,
            require_authorized=True,
        )


def test_pre_execution_snapshot_rejects_ready_binding_drift() -> None:
    state = _authorized_state()
    ready = _ready()
    snapshot = evaluate_pre_execution_authorization(
        ready_verification=ready,
        project_state=state,
    )
    changed_ready = _ready()
    changed_ready["origin_attestation_sha256"] = "8" * 64

    with pytest.raises(PreExecutionError, match="stale or modified"):
        verify_pre_execution_snapshot(
            snapshot,
            ready_verification=changed_ready,
            project_state=state,
            require_authorized=True,
        )


def test_pre_execution_snapshot_rejects_tampering_and_blocked_require_authorized() -> None:
    state = _authorized_state()
    ready = _ready()
    snapshot = evaluate_pre_execution_authorization(
        ready_verification=ready,
        project_state=state,
    )
    tampered = dict(snapshot)
    tampered["authorization_report_sha256"] = "0" * 64
    with pytest.raises(PreExecutionError, match="stale or modified"):
        verify_pre_execution_snapshot(
            tampered,
            ready_verification=ready,
            project_state=state,
            require_authorized=True,
        )

    blocked_state = copy.deepcopy(state)
    phase = blocked_state["phase_15_v3_live_canary"]
    assert isinstance(phase, dict)
    phase["telegram_one_tap_submission_authorized"] = False
    blocked = evaluate_pre_execution_authorization(
        ready_verification=ready,
        project_state=blocked_state,
    )
    with pytest.raises(PreExecutionError, match="not authorized"):
        verify_pre_execution_snapshot(
            blocked,
            ready_verification=ready,
            project_state=blocked_state,
            require_authorized=True,
        )
