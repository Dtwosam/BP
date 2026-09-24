from __future__ import annotations

import copy
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bp_engine.execution.telegram_approval import approval_record, new_pending
from bp_engine.execution.telegram_origin_attestation import (
    OriginAttestationError,
    create_origin_attestation,
    encode_origin_key,
    execution_approval,
    load_origin_key_file,
    payload_sha256,
    verify_origin_attestation,
)

ORIGIN_KEY_ID = "phase15-telegram-origin-v1"


def _prepared(now: datetime) -> dict[str, object]:
    return {
        "status": "prepared",
        "action": "submit",
        "intent_id": "live-intent-origin",
        "prediction_id": "prediction-origin",
        "paper_order_id": "paper-origin",
        "market_end_at": (now + timedelta(seconds=55)).isoformat(),
        "request": {
            "selected_side": "down",
            "limit_price": "0.72",
            "requested_shares": "6.81",
            "target_notional_usd": "5",
            "token_id": "token-origin",
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
        callback_query_id="callback-id",
        approved_at=now + timedelta(seconds=1),
    )


def test_origin_key_file_is_restricted_and_non_symlink(tmp_path: Path) -> None:
    key = bytes(range(32))
    path = tmp_path / "origin.key"
    path.write_text(encode_origin_key(key) + "\n", encoding="utf-8")
    path.chmod(0o600)
    assert load_origin_key_file(path) == key

    path.chmod(0o640)
    assert load_origin_key_file(path) == key

    path.chmod(0o644)
    with pytest.raises(OriginAttestationError, match="mode must be 0600 or 0640"):
        load_origin_key_file(path)

    path.chmod(0o600)
    link = tmp_path / "origin-link.key"
    link.symlink_to(path)
    with pytest.raises(OriginAttestationError, match="regular non-symlink"):
        load_origin_key_file(link)


def test_origin_attestation_binds_exact_approval_without_telegram_identity() -> None:
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    prepared = _prepared(now)
    approval = _approval(prepared, now)
    key = bytes(range(32, 64))

    attestation = create_origin_attestation(
        prepared,
        approval=approval,
        key=key,
        key_id=ORIGIN_KEY_ID,
        attested_at=now + timedelta(seconds=2),
    )
    sanitized = execution_approval(approval)
    verified = verify_origin_attestation(
        attestation,
        prepared=prepared,
        approval=sanitized,
        key=key,
        expected_key_id=ORIGIN_KEY_ID,
        observed_at=now + timedelta(seconds=3),
    )

    assert verified["intent_id"] == prepared["intent_id"]
    assert verified["prepared_sha256"] == payload_sha256(prepared)
    assert verified["approval_sha256"] == payload_sha256(sanitized)
    assert attestation["approval_source_sha256"] == payload_sha256(approval)
    serialized = json.dumps(attestation, sort_keys=True)
    assert "telegram_user_id" not in serialized
    assert "telegram_chat_id" not in serialized
    assert "callback_query_id" not in serialized


def test_origin_attestation_tamper_wrong_key_id_and_expiry_fail_closed() -> None:
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    prepared = _prepared(now)
    approval = _approval(prepared, now)
    sanitized = execution_approval(approval)
    key = bytes(range(32, 64))
    attestation = create_origin_attestation(
        prepared,
        approval=approval,
        key=key,
        key_id=ORIGIN_KEY_ID,
        attested_at=now + timedelta(seconds=2),
    )

    unexpected = copy.deepcopy(attestation)
    unexpected["unexpected"] = True
    with pytest.raises(OriginAttestationError, match="fields mismatch"):
        verify_origin_attestation(
            unexpected,
            prepared=prepared,
            approval=sanitized,
            key=key,
            expected_key_id=ORIGIN_KEY_ID,
            observed_at=now + timedelta(seconds=3),
        )

    modified = copy.deepcopy(attestation)
    modified["request_sha256"] = "0" * 64
    with pytest.raises(OriginAttestationError, match="hmac mismatch"):
        verify_origin_attestation(
            modified,
            prepared=prepared,
            approval=sanitized,
            key=key,
            expected_key_id=ORIGIN_KEY_ID,
            observed_at=now + timedelta(seconds=3),
        )

    with pytest.raises(OriginAttestationError, match="key id mismatch"):
        verify_origin_attestation(
            attestation,
            prepared=prepared,
            approval=sanitized,
            key=key,
            expected_key_id="phase15-telegram-origin-v2",
            observed_at=now + timedelta(seconds=3),
        )

    expires = datetime.fromisoformat(str(attestation["expires_at"]))
    with pytest.raises(OriginAttestationError, match="expired"):
        verify_origin_attestation(
            attestation,
            prepared=prepared,
            approval=sanitized,
            key=key,
            expected_key_id=ORIGIN_KEY_ID,
            observed_at=expires,
        )


def test_transport_key_cannot_forge_origin_attestation() -> None:
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    prepared = _prepared(now)
    approval = _approval(prepared, now)
    sanitized = execution_approval(approval)
    origin_key = bytes(range(32, 64))
    transport_key = bytes(range(32))
    attestation = create_origin_attestation(
        prepared,
        approval=approval,
        key=origin_key,
        key_id=ORIGIN_KEY_ID,
        attested_at=now + timedelta(seconds=2),
    )

    with pytest.raises(OriginAttestationError, match="hmac mismatch"):
        verify_origin_attestation(
            attestation,
            prepared=prepared,
            approval=sanitized,
            key=transport_key,
            expected_key_id=ORIGIN_KEY_ID,
            observed_at=now + timedelta(seconds=3),
        )


def test_origin_attestation_source_has_no_network_or_execution_dependencies() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "bp_engine"
        / "execution"
        / "telegram_origin_attestation.py"
    )
    text = path.read_text(encoding="utf-8")
    compile(text, str(path), "exec")
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
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_WALLET_ADDRESS",
    ):
        assert forbidden not in text
    assert os.path.basename(path) == "telegram_origin_attestation.py"
