from __future__ import annotations

import copy
import json
import os
from datetime import UTC, datetime, timedelta

import pytest

from bp_engine.execution.telegram_approval import (
    approval_record,
    new_pending,
    request_sha256,
)
from bp_engine.execution.telegram_transport import (
    TransportError,
    claim_transport_envelope,
    create_transport_envelope,
    encode_transport_key,
    parse_transport_key,
    payload_sha256,
    verify_transport_envelope,
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


def _approval(prepared: dict[str, object], now: datetime) -> dict[str, object]:
    pending = new_pending(
        prepared,
        telegram_user_id=111,
        telegram_chat_id=111,
        created_at=now,
        nonce="approval-nonce",
    )
    return approval_record(
        action="approve",
        pending=pending,
        callback_query_id="callback-secret-ish-id",
        approved_at=now + timedelta(seconds=1),
    )


def test_transport_key_round_trip_is_exact_and_strict() -> None:
    key = bytes(range(32))
    encoded = encode_transport_key(key)
    assert parse_transport_key(encoded) == key

    with pytest.raises(TransportError, match="exactly 32 bytes"):
        parse_transport_key(encode_transport_key(bytes(range(31))))
    with pytest.raises(TransportError, match="valid base64url"):
        parse_transport_key("%%%not-base64%%%")


def test_transport_envelope_is_exact_bound_and_strips_telegram_identity() -> None:
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    prepared = _prepared(now)
    approval = _approval(prepared, now)
    key = bytes(range(32))

    envelope = create_transport_envelope(
        prepared,
        approval=approval,
        key=key,
        created_at=now + timedelta(seconds=2),
        nonce="transport-nonce-1",
    )
    verified = verify_transport_envelope(
        envelope,
        key=key,
        observed_at=now + timedelta(seconds=3),
    )

    assert verified["intent_id"] == prepared["intent_id"]
    assert verified["request_sha256"] == request_sha256(prepared)
    assert envelope["prepared_sha256"] == payload_sha256(prepared)
    assert envelope["approval_source_sha256"] == payload_sha256(approval)
    assert "telegram_user_id" not in envelope["approval"]
    assert "telegram_chat_id" not in envelope["approval"]
    assert "callback_query_id" not in envelope["approval"]


def test_transport_envelope_tampering_wrong_key_and_expiry_fail_closed() -> None:
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    prepared = _prepared(now)
    approval = _approval(prepared, now)
    key = bytes(range(32))
    envelope = create_transport_envelope(
        prepared,
        approval=approval,
        key=key,
        created_at=now + timedelta(seconds=2),
        nonce="transport-nonce-1",
    )

    modified = copy.deepcopy(envelope)
    modified["prepared"]["request"]["limit_price"] = "0.71"
    with pytest.raises(TransportError, match="hmac mismatch"):
        verify_transport_envelope(
            modified,
            key=key,
            observed_at=now + timedelta(seconds=3),
        )

    with pytest.raises(TransportError, match="hmac mismatch"):
        verify_transport_envelope(
            envelope,
            key=bytes(reversed(range(32))),
            observed_at=now + timedelta(seconds=3),
        )

    expires = datetime.fromisoformat(str(envelope["expires_at"]))
    with pytest.raises(TransportError, match="expired"):
        verify_transport_envelope(
            envelope,
            key=key,
            observed_at=expires,
        )


def test_transport_claim_is_one_shot_for_exact_order_even_with_new_nonce(tmp_path) -> None:
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    prepared = _prepared(now)
    approval = _approval(prepared, now)
    key = bytes(range(32))
    state_dir = tmp_path / "claims"

    first = create_transport_envelope(
        prepared,
        approval=approval,
        key=key,
        created_at=now + timedelta(seconds=2),
        nonce="transport-nonce-1",
    )
    second = create_transport_envelope(
        prepared,
        approval=approval,
        key=key,
        created_at=now + timedelta(seconds=3),
        nonce="transport-nonce-2",
    )

    claimed = claim_transport_envelope(
        first,
        key=key,
        observed_at=now + timedelta(seconds=4),
        state_dir=state_dir,
    )
    claim_path = state_dir / str(claimed["claim_path"]).split("/")[-1]
    assert claim_path.is_file()
    assert (os.stat(state_dir).st_mode & 0o777) == 0o700
    assert (os.stat(claim_path).st_mode & 0o777) == 0o600
    record = json.loads(claim_path.read_text(encoding="utf-8"))
    assert record["retry_allowed"] is False
    assert record["intent_id"] == prepared["intent_id"]
    assert record["request_sha256"] == request_sha256(prepared)

    with pytest.raises(TransportError, match="already claimed"):
        claim_transport_envelope(
            second,
            key=key,
            observed_at=now + timedelta(seconds=4),
            state_dir=state_dir,
        )
