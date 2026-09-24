from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bp_engine.execution.telegram_approval import (
    ApprovalError,
    approval_record,
    build_prompt,
    callback_data,
    new_pending,
    request_sha256,
    validate_callback,
    validate_prepared,
)


def _prepared(now: datetime) -> dict[str, object]:
    return {
        "status": "prepared",
        "action": "submit",
        "intent_id": "live-intent-123",
        "prediction_id": "prediction-123",
        "paper_order_id": "paper-123",
        "market_end_at": (now + timedelta(seconds=50)).isoformat(),
        "request": {
            "selected_side": "down",
            "limit_price": "0.72",
            "requested_shares": "6.81",
            "target_notional_usd": "5",
            "token_id": "token-123",
            "action": "BUY",
        },
        "policy": {
            "policy_version": "v3-live-canary-v1",
            "max_submission_attempts": 1,
        },
    }


def _callback(*, user_id: int, chat_id: int, data: str) -> dict[str, object]:
    return {
        "update_id": 7,
        "callback_query": {
            "id": "cb-1",
            "from": {"id": user_id},
            "message": {"message_id": 9, "chat": {"id": chat_id, "type": "private"}},
            "data": data,
        },
    }


def test_prepared_validation_and_prompt_are_bound_to_request() -> None:
    now = datetime(2026, 9, 24, 20, 0, tzinfo=UTC)
    prepared = _prepared(now)
    validated = validate_prepared(prepared, observed_at=now)
    assert validated["intent_id"] == "live-intent-123"
    assert validated["request_sha256"] == request_sha256(prepared)
    prompt = build_prompt(prepared, observed_at=now)
    assert "DOWN" in prompt
    assert "Maximum spend: $5" in prompt
    assert "50.0s" in prompt


def test_callback_accepts_only_exact_private_user_chat_and_nonce() -> None:
    now = datetime(2026, 9, 24, 20, 0, tzinfo=UTC)
    pending = new_pending(
        _prepared(now),
        telegram_user_id=111,
        telegram_chat_id=222,
        created_at=now,
        nonce="nonce123",
    )
    update = _callback(
        user_id=111,
        chat_id=222,
        data=callback_data("approve", "nonce123"),
    )
    assert (
        validate_callback(update, pending=pending, observed_at=now + timedelta(seconds=1))
        == "approve"
    )

    wrong_user = _callback(
        user_id=999,
        chat_id=222,
        data=callback_data("approve", "nonce123"),
    )
    with pytest.raises(ApprovalError, match="user mismatch"):
        validate_callback(
            wrong_user,
            pending=pending,
            observed_at=now + timedelta(seconds=1),
        )

    wrong_nonce = _callback(
        user_id=111,
        chat_id=222,
        data=callback_data("approve", "other"),
    )
    with pytest.raises(ApprovalError, match="nonce/action mismatch"):
        validate_callback(
            wrong_nonce,
            pending=pending,
            observed_at=now + timedelta(seconds=1),
        )


def test_approval_record_is_exact_and_one_intent_bound() -> None:
    now = datetime(2026, 9, 24, 20, 0, tzinfo=UTC)
    prepared = _prepared(now)
    pending = new_pending(
        prepared,
        telegram_user_id=111,
        telegram_chat_id=222,
        created_at=now,
        nonce="nonce123",
    )
    record = approval_record(
        action="approve",
        pending=pending,
        callback_query_id="cb-1",
        approved_at=now + timedelta(seconds=2),
    )
    assert record["status"] == "approved"
    assert record["intent_id"] == prepared["intent_id"]
    assert record["request_sha256"] == request_sha256(prepared)
    assert record["telegram_user_id"] == 111
    assert record["telegram_chat_id"] == 222


def test_expired_and_changed_target_fail_closed() -> None:
    now = datetime(2026, 9, 24, 20, 0, tzinfo=UTC)
    prepared = _prepared(now)
    pending = new_pending(
        prepared,
        telegram_user_id=111,
        telegram_chat_id=222,
        created_at=now,
        nonce="nonce123",
    )
    update = _callback(
        user_id=111,
        chat_id=222,
        data=callback_data("approve", "nonce123"),
    )
    with pytest.raises(ApprovalError, match="expired"):
        validate_callback(
            update,
            pending=pending,
            observed_at=datetime.fromisoformat(str(pending["expires_at"])),
        )

    request = prepared["request"]
    assert isinstance(request, dict)
    request["target_notional_usd"] = "6"
    with pytest.raises(ApprovalError, match="target notional changed"):
        validate_prepared(prepared, observed_at=now)
