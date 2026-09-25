from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bp_engine.execution.telegram_approval import approval_record, new_pending
from bp_engine.execution.telegram_origin_attestation import (
    create_origin_attestation,
)
from bp_engine.execution.telegram_pre_execution import source_truth_sha256
from bp_engine.execution.telegram_source_truth_authorization import (
    SOURCE_TRUTH_AUTHORIZATION_MAX_LIFETIME_SECONDS,
    SourceTruthAuthorizationError,
    create_source_truth_authorization,
    verify_source_truth_authorization,
)

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "PROJECT_STATE.json"
ORIGIN_KEY_ID = "phase15-telegram-origin-v1"


def _prepared(now: datetime) -> dict[str, object]:
    return {
        "status": "prepared",
        "action": "submit",
        "intent_id": "live-intent-source-truth",
        "prediction_id": "prediction-source-truth",
        "paper_order_id": "paper-source-truth",
        "market_end_at": (now + timedelta(seconds=55)).isoformat(),
        "request": {
            "selected_side": "down",
            "limit_price": "0.72",
            "requested_shares": "6.81",
            "target_notional_usd": "5",
            "token_id": "token-source-truth",
            "action": "BUY",
        },
        "policy": {
            "policy_version": "v3-live-canary-v1",
            "max_submission_attempts": 1,
        },
    }


def _approval(prepared: dict[str, object], now: datetime) -> dict[str, object]:
    pending = new_pending(
        prepared,
        telegram_user_id=111,
        telegram_chat_id=111,
        created_at=now,
        nonce="source-truth-nonce",
    )
    return approval_record(
        action="approve",
        pending=pending,
        callback_query_id="source-truth-callback",
        approved_at=now + timedelta(seconds=1),
    )


def _authorized_state() -> dict[str, object]:
    return {
        "source_of_truth_version": "synthetic-authorized",
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


def _origin(
    prepared: dict[str, object],
    approval: dict[str, object],
    now: datetime,
    key: bytes,
) -> dict[str, object]:
    return create_origin_attestation(
        prepared,
        approval=approval,
        key=key,
        key_id=ORIGIN_KEY_ID,
        attested_at=now + timedelta(seconds=2),
    )


def test_current_source_truth_is_signed_as_blocked_and_cannot_be_required() -> None:
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    state = json.loads(STATE.read_text(encoding="utf-8"))
    prepared = _prepared(now)
    approval = _approval(prepared, now)
    key = bytes(range(32, 64))
    origin = _origin(prepared, approval, now, key)

    attestation = create_source_truth_authorization(
        state,
        prepared=prepared,
        approval=approval,
        origin_attestation=origin,
        key=key,
        key_id=ORIGIN_KEY_ID,
        attested_at=now + timedelta(seconds=3),
    )
    verified = verify_source_truth_authorization(
        attestation,
        prepared=prepared,
        approval=approval,
        origin_attestation=origin,
        key=key,
        expected_key_id=ORIGIN_KEY_ID,
        observed_at=now + timedelta(seconds=4),
    )

    assert verified["authorized"] is False
    assert verified["project_state_sha256"] == source_truth_sha256(state)
    for blocker in (
        "second_order_not_authorized",
        "automated_real_money_submission_not_authorized",
        "manual_submission_still_required",
        "telegram_one_tap_not_authorized",
        "persistent_execution_transport_not_authorized",
        "telegram_pubsub_transport_not_authorized",
    ):
        assert blocker in verified["blockers"]

    with pytest.raises(
        SourceTruthAuthorizationError,
        match="source truth snapshot is not authorized",
    ):
        verify_source_truth_authorization(
            attestation,
            prepared=prepared,
            approval=approval,
            origin_attestation=origin,
            key=key,
            expected_key_id=ORIGIN_KEY_ID,
            observed_at=now + timedelta(seconds=4),
            require_authorized=True,
        )


def test_authorized_snapshot_is_exact_order_bound_and_short_lived() -> None:
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    state = _authorized_state()
    prepared = _prepared(now)
    approval = _approval(prepared, now)
    key = bytes(range(32, 64))
    origin = _origin(prepared, approval, now, key)

    attestation = create_source_truth_authorization(
        state,
        prepared=prepared,
        approval=approval,
        origin_attestation=origin,
        key=key,
        key_id=ORIGIN_KEY_ID,
        attested_at=now + timedelta(seconds=3),
    )
    verified = verify_source_truth_authorization(
        attestation,
        prepared=prepared,
        approval=approval,
        origin_attestation=origin,
        key=key,
        expected_key_id=ORIGIN_KEY_ID,
        observed_at=now + timedelta(seconds=4),
        require_authorized=True,
    )

    assert verified["authorized"] is True
    assert verified["blockers"] == []
    assert verified["intent_id"] == prepared["intent_id"]
    assert verified["request_sha256"] == approval["request_sha256"]
    attested_at = datetime.fromisoformat(str(verified["attested_at"]))
    expires_at = datetime.fromisoformat(str(verified["expires_at"]))
    origin_expires = datetime.fromisoformat(str(origin["expires_at"]))
    assert (
        expires_at - attested_at
    ).total_seconds() <= SOURCE_TRUTH_AUTHORIZATION_MAX_LIFETIME_SECONDS
    assert expires_at <= origin_expires


def test_source_truth_authorization_tamper_wrong_key_and_order_fail_closed() -> None:
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    state = _authorized_state()
    prepared = _prepared(now)
    approval = _approval(prepared, now)
    key = bytes(range(32, 64))
    origin = _origin(prepared, approval, now, key)
    attestation = create_source_truth_authorization(
        state,
        prepared=prepared,
        approval=approval,
        origin_attestation=origin,
        key=key,
        key_id=ORIGIN_KEY_ID,
        attested_at=now + timedelta(seconds=3),
    )

    tampered = copy.deepcopy(attestation)
    snapshot = tampered["authorization_snapshot"]
    assert isinstance(snapshot, dict)
    snapshot["second_order_authorized"] = False
    with pytest.raises(
        SourceTruthAuthorizationError,
        match="hmac mismatch",
    ):
        verify_source_truth_authorization(
            tampered,
            prepared=prepared,
            approval=approval,
            origin_attestation=origin,
            key=key,
            expected_key_id=ORIGIN_KEY_ID,
            observed_at=now + timedelta(seconds=4),
        )

    with pytest.raises(
        SourceTruthAuthorizationError,
        match="hmac mismatch",
    ):
        verify_source_truth_authorization(
            attestation,
            prepared=prepared,
            approval=approval,
            origin_attestation=origin,
            key=bytes(range(32)),
            expected_key_id=ORIGIN_KEY_ID,
            observed_at=now + timedelta(seconds=4),
        )

    changed_prepared = copy.deepcopy(prepared)
    request = changed_prepared["request"]
    assert isinstance(request, dict)
    request["limit_price"] = "0.71"
    with pytest.raises(SourceTruthAuthorizationError):
        verify_source_truth_authorization(
            attestation,
            prepared=changed_prepared,
            approval=approval,
            origin_attestation=origin,
            key=key,
            expected_key_id=ORIGIN_KEY_ID,
            observed_at=now + timedelta(seconds=4),
        )


def test_source_truth_authorization_expiry_and_state_hash_drift_are_explicit() -> None:
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    state = _authorized_state()
    prepared = _prepared(now)
    approval = _approval(prepared, now)
    key = bytes(range(32, 64))
    origin = _origin(prepared, approval, now, key)
    first = create_source_truth_authorization(
        state,
        prepared=prepared,
        approval=approval,
        origin_attestation=origin,
        key=key,
        key_id=ORIGIN_KEY_ID,
        attested_at=now + timedelta(seconds=3),
    )
    expires = datetime.fromisoformat(str(first["expires_at"]))
    with pytest.raises(SourceTruthAuthorizationError, match="expired"):
        verify_source_truth_authorization(
            first,
            prepared=prepared,
            approval=approval,
            origin_attestation=origin,
            key=key,
            expected_key_id=ORIGIN_KEY_ID,
            observed_at=expires,
        )

    changed = copy.deepcopy(state)
    phase = changed["phase_15_v3_live_canary"]
    assert isinstance(phase, dict)
    phase["second_order_authorized"] = False
    second = create_source_truth_authorization(
        changed,
        prepared=prepared,
        approval=approval,
        origin_attestation=origin,
        key=key,
        key_id=ORIGIN_KEY_ID,
        attested_at=now + timedelta(seconds=3),
    )
    assert first["project_state_sha256"] != second["project_state_sha256"]
    assert first["authorization_snapshot_sha256"] != second[
        "authorization_snapshot_sha256"
    ]
    assert second["authorized"] is False


def test_source_truth_authorization_contains_no_telegram_identity() -> None:
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    prepared = _prepared(now)
    approval = _approval(prepared, now)
    key = bytes(range(32, 64))
    origin = _origin(prepared, approval, now, key)
    attestation = create_source_truth_authorization(
        _authorized_state(),
        prepared=prepared,
        approval=approval,
        origin_attestation=origin,
        key=key,
        key_id=ORIGIN_KEY_ID,
        attested_at=now + timedelta(seconds=3),
    )

    serialized = json.dumps(attestation, sort_keys=True)
    assert "telegram_user_id" not in serialized
    assert "telegram_chat_id" not in serialized
    assert "callback_query_id" not in serialized
