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
from bp_engine.execution.telegram_origin_attestation import create_origin_attestation
from bp_engine.execution.telegram_transport import (
    TransportError,
    claim_transport_envelope,
    create_transport_envelope,
    encode_transport_key,
    load_transport_key_file,
    parse_transport_key,
    payload_sha256,
    verify_transport_envelope,
)

KEY_ID = "phase15-telegram-transport-v1"
ORIGIN_KEY_ID = "phase15-telegram-origin-v1"
ORIGIN_KEY = bytes(range(32, 64))


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


def _origin_attestation(
    prepared: dict[str, object],
    approval: dict[str, object],
    now: datetime,
) -> dict[str, object]:
    return create_origin_attestation(
        prepared,
        approval=approval,
        key=ORIGIN_KEY,
        key_id=ORIGIN_KEY_ID,
        attested_at=now + timedelta(seconds=2),
    )


def test_transport_key_round_trip_is_exact_and_strict() -> None:
    key = bytes(range(32))
    encoded = encode_transport_key(key)
    assert parse_transport_key(encoded) == key

    with pytest.raises(TransportError, match="exactly 32 bytes"):
        parse_transport_key(encode_transport_key(bytes(range(31))))
    with pytest.raises(TransportError, match="valid base64url"):
        parse_transport_key("%%%not-base64%%%")


def test_transport_key_file_rejects_weak_mode_and_symlink(tmp_path) -> None:
    key_path = tmp_path / "transport.key"
    key_path.write_text(encode_transport_key(bytes(range(32))) + "\n", encoding="utf-8")
    key_path.chmod(0o600)
    assert load_transport_key_file(key_path) == bytes(range(32))

    key_path.chmod(0o644)
    with pytest.raises(TransportError, match="mode"):
        load_transport_key_file(key_path)

    key_path.chmod(0o600)
    link = tmp_path / "transport-link.key"
    link.symlink_to(key_path)
    with pytest.raises(TransportError, match="non-symlink"):
        load_transport_key_file(link)


def test_transport_envelope_is_exact_bound_and_strips_telegram_identity() -> None:
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    prepared = _prepared(now)
    approval = _approval(prepared, now)
    key = bytes(range(32))

    envelope = create_transport_envelope(
        prepared,
        approval=approval,
        origin_attestation=_origin_attestation(prepared, approval, now),
        key=key,
        key_id=KEY_ID,
        created_at=now + timedelta(seconds=2),
        nonce="transport-nonce-1",
    )
    verified = verify_transport_envelope(
        envelope,
        key=key,
        expected_key_id=KEY_ID,
        observed_at=now + timedelta(seconds=3),
    )

    assert verified["key_id"] == KEY_ID
    assert verified["intent_id"] == prepared["intent_id"]
    assert verified["request_sha256"] == request_sha256(prepared)
    assert envelope["prepared_sha256"] == payload_sha256(prepared)
    assert envelope["approval_source_sha256"] == payload_sha256(approval)
    assert verified["origin_attestation"] == envelope["origin_attestation"]
    assert verified["origin_attestation_sha256"] == envelope["origin_attestation_sha256"]
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
        origin_attestation=_origin_attestation(prepared, approval, now),
        key=key,
        key_id=KEY_ID,
        created_at=now + timedelta(seconds=2),
        nonce="transport-nonce-1",
    )

    unexpected = copy.deepcopy(envelope)
    unexpected["unexpected_field"] = "not-allowed"
    with pytest.raises(TransportError, match="envelope fields mismatch"):
        verify_transport_envelope(
            unexpected,
            key=key,
            expected_key_id=KEY_ID,
            observed_at=now + timedelta(seconds=3),
        )

    origin_modified = copy.deepcopy(envelope)
    origin_modified["origin_attestation"]["intent_id"] = "forged-intent"
    with pytest.raises(TransportError, match="hmac mismatch"):
        verify_transport_envelope(
            origin_modified,
            key=key,
            expected_key_id=KEY_ID,
            observed_at=now + timedelta(seconds=3),
        )

    modified = copy.deepcopy(envelope)
    modified["prepared"]["request"]["limit_price"] = "0.71"
    with pytest.raises(TransportError, match="hmac mismatch"):
        verify_transport_envelope(
            modified,
            key=key,
            expected_key_id=KEY_ID,
            observed_at=now + timedelta(seconds=3),
        )

    with pytest.raises(TransportError, match="hmac mismatch"):
        verify_transport_envelope(
            envelope,
            key=bytes(reversed(range(32))),
            expected_key_id=KEY_ID,
            observed_at=now + timedelta(seconds=3),
        )

    with pytest.raises(TransportError, match="key id mismatch"):
        verify_transport_envelope(
            envelope,
            key=key,
            expected_key_id="phase15-telegram-transport-v2",
            observed_at=now + timedelta(seconds=3),
        )

    expires = datetime.fromisoformat(str(envelope["expires_at"]))
    with pytest.raises(TransportError, match="expired"):
        verify_transport_envelope(
            envelope,
            key=key,
            expected_key_id=KEY_ID,
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
        origin_attestation=_origin_attestation(prepared, approval, now),
        key=key,
        key_id=KEY_ID,
        created_at=now + timedelta(seconds=2),
        nonce="transport-nonce-1",
    )
    second = create_transport_envelope(
        prepared,
        approval=approval,
        origin_attestation=_origin_attestation(prepared, approval, now),
        key=key,
        key_id=KEY_ID,
        created_at=now + timedelta(seconds=3),
        nonce="transport-nonce-2",
    )

    claimed = claim_transport_envelope(
        first,
        key=key,
        expected_key_id=KEY_ID,
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
            expected_key_id=KEY_ID,
            origin_key=ORIGIN_KEY,
            expected_origin_key_id=ORIGIN_KEY_ID,
            observed_at=now + timedelta(seconds=4),
            state_dir=state_dir,
        )


def test_transport_claim_rejects_symlink_state_directory(tmp_path) -> None:
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    prepared = _prepared(now)
    approval = _approval(prepared, now)
    key = bytes(range(32))
    envelope = create_transport_envelope(
        prepared,
        approval=approval,
        origin_attestation=_origin_attestation(prepared, approval, now),
        key=key,
        key_id=KEY_ID,
        created_at=now + timedelta(seconds=2),
        nonce="transport-nonce-symlink",
    )
    actual = tmp_path / "actual"
    actual.mkdir()
    link = tmp_path / "claims"
    link.symlink_to(actual, target_is_directory=True)

    with pytest.raises(TransportError, match="non-symlink directory"):
        claim_transport_envelope(
            envelope,
            key=key,
            expected_key_id=KEY_ID,
            origin_key=ORIGIN_KEY,
            expected_origin_key_id=ORIGIN_KEY_ID,
            observed_at=now + timedelta(seconds=3),
            state_dir=link,
        )


def test_transport_claim_carries_but_does_not_authenticate_origin_hmac(tmp_path) -> None:
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    prepared = _prepared(now)
    approval = _approval(prepared, now)
    forged_origin = create_origin_attestation(
        prepared,
        approval=approval,
        key=bytes(range(64, 96)),
        key_id=ORIGIN_KEY_ID,
        attested_at=now + timedelta(seconds=2),
    )
    transport_key = bytes(range(32))
    envelope = create_transport_envelope(
        prepared,
        approval=approval,
        origin_attestation=forged_origin,
        key=transport_key,
        key_id=KEY_ID,
        created_at=now + timedelta(seconds=2),
        nonce="transport-forged-origin",
    )
    state_dir = tmp_path / "claims"

    claimed = claim_transport_envelope(
        envelope,
        key=transport_key,
        expected_key_id=KEY_ID,
        observed_at=now + timedelta(seconds=3),
        state_dir=state_dir,
    )
    assert claimed["origin_attestation"] == forged_origin
    assert claimed["retry_allowed"] is False
    assert Path(str(claimed["claim_path"])).is_file()

