from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bp_engine.execution.telegram_approval import approval_record, new_pending
from bp_engine.execution.telegram_execution_ready import (
    ReadyVerificationError,
    verify_ready_bundle,
)
from bp_engine.execution.telegram_origin_attestation import (
    create_origin_attestation,
    encode_origin_key,
)
from bp_engine.execution.telegram_source_truth_authorization import (
    create_source_truth_authorization,
)
from bp_engine.execution.telegram_transport import (
    create_authorized_transport_envelope,
    create_transport_envelope,
)

ORIGIN_KEY_ID = "phase15-telegram-origin-v1"
TRANSPORT_KEY_ID = "phase15-telegram-transport-v1"


def _prepared(now: datetime) -> dict[str, object]:
    return {
        "status": "prepared",
        "action": "submit",
        "intent_id": "live-intent-ready-module",
        "prediction_id": "prediction-ready-module",
        "paper_order_id": "paper-ready-module",
        "market_end_at": (now + timedelta(seconds=55)).isoformat(),
        "request": {
            "selected_side": "down",
            "limit_price": "0.72",
            "requested_shares": "6.81",
            "target_notional_usd": "5",
            "token_id": "token-ready-module",
            "action": "BUY",
        },
        "policy": {
            "policy_version": "v3-live-canary-v1",
            "max_submission_attempts": 1,
        },
    }


def _authorized_state() -> dict[str, object]:
    return {
        "source_of_truth_version": "synthetic-ready-v2",
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


def _bundle(
    tmp_path: Path,
    now: datetime,
    *,
    signing_key: bytes | None = None,
    verification_key: bytes | None = None,
) -> tuple[Path, Path]:
    prepared = _prepared(now)
    pending = new_pending(
        prepared,
        telegram_user_id=111,
        telegram_chat_id=111,
        created_at=now,
        nonce="ready-module-approval",
    )
    approval = approval_record(
        action="approve",
        pending=pending,
        callback_query_id="callback-id",
        approved_at=now + timedelta(seconds=1),
    )
    origin_key = signing_key or bytes(range(32, 64))
    origin = create_origin_attestation(
        prepared,
        approval=approval,
        key=origin_key,
        key_id=ORIGIN_KEY_ID,
        attested_at=now + timedelta(seconds=2),
    )
    envelope = create_transport_envelope(
        prepared,
        approval=approval,
        origin_attestation=origin,
        key=bytes(range(32)),
        key_id=TRANSPORT_KEY_ID,
        created_at=now + timedelta(seconds=2),
        nonce="ready-module-transport",
    )
    receipt = {
        "schema_version": 1,
        "status": "claimed_ready",
        "key_id": TRANSPORT_KEY_ID,
        "origin_key_id": ORIGIN_KEY_ID,
        "intent_id": envelope["intent_id"],
        "prediction_id": envelope["prediction_id"],
        "paper_order_id": envelope["paper_order_id"],
        "request_sha256": envelope["request_sha256"],
        "prepared_sha256": envelope["prepared_sha256"],
        "approval_sha256": envelope["approval_sha256"],
        "approval_source_sha256": envelope["approval_source_sha256"],
        "origin_attestation_sha256": envelope["origin_attestation_sha256"],
        "retry_allowed": False,
        "executor_invoked": False,
        "real_order_submitted": False,
    }

    ready = tmp_path / "ready"
    ready.mkdir(parents=True, mode=0o700)
    payloads = {
        "prepared.json": prepared,
        "approval.json": envelope["approval"],
        "origin-attestation.json": origin,
        "envelope.json": envelope,
        "receipt.json": receipt,
    }
    for name, payload in payloads.items():
        path = ready / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        path.chmod(0o600)

    key_path = tmp_path / "origin.key"
    key_file_value = verification_key or origin_key
    key_path.write_text(encode_origin_key(key_file_value) + "\n", encoding="utf-8")
    key_path.chmod(0o600)
    return ready, key_path


def _bundle_v2(
    tmp_path: Path,
    now: datetime,
    *,
    alter_source_mac: bool = False,
) -> tuple[Path, Path]:
    prepared = _prepared(now)
    pending = new_pending(
        prepared,
        telegram_user_id=111,
        telegram_chat_id=111,
        created_at=now,
        nonce="ready-v2-approval",
    )
    approval = approval_record(
        action="approve",
        pending=pending,
        callback_query_id="ready-v2-callback",
        approved_at=now + timedelta(seconds=1),
    )
    origin_key = bytes(range(32, 64))
    origin = create_origin_attestation(
        prepared,
        approval=approval,
        key=origin_key,
        key_id=ORIGIN_KEY_ID,
        attested_at=now + timedelta(seconds=2),
    )
    source_truth = create_source_truth_authorization(
        _authorized_state(),
        prepared=prepared,
        approval=approval,
        origin_attestation=origin,
        key=origin_key,
        key_id=ORIGIN_KEY_ID,
        attested_at=now + timedelta(seconds=3),
    )
    if alter_source_mac:
        source_truth["hmac_sha256"] = "0" * 64
    envelope = create_authorized_transport_envelope(
        prepared,
        approval=approval,
        origin_attestation=origin,
        source_truth_authorization=source_truth,
        key=bytes(range(32)),
        key_id=TRANSPORT_KEY_ID,
        created_at=now + timedelta(seconds=3),
        nonce="ready-v2-transport",
    )
    receipt = {
        "schema_version": 1,
        "status": "claimed_ready",
        "key_id": TRANSPORT_KEY_ID,
        "origin_key_id": ORIGIN_KEY_ID,
        "intent_id": envelope["intent_id"],
        "prediction_id": envelope["prediction_id"],
        "paper_order_id": envelope["paper_order_id"],
        "request_sha256": envelope["request_sha256"],
        "prepared_sha256": envelope["prepared_sha256"],
        "approval_sha256": envelope["approval_sha256"],
        "approval_source_sha256": envelope["approval_source_sha256"],
        "origin_attestation_sha256": envelope["origin_attestation_sha256"],
        "source_truth_authorization_sha256": envelope[
            "source_truth_authorization_sha256"
        ],
        "project_state_sha256": source_truth["project_state_sha256"],
        "authorization_snapshot_sha256": source_truth[
            "authorization_snapshot_sha256"
        ],
        "retry_allowed": False,
        "executor_invoked": False,
        "real_order_submitted": False,
    }
    ready = tmp_path / "ready-v2"
    ready.mkdir(parents=True, mode=0o700)
    payloads = {
        "prepared.json": prepared,
        "approval.json": envelope["approval"],
        "origin-attestation.json": origin,
        "source-truth-authorization.json": source_truth,
        "envelope.json": envelope,
        "receipt.json": receipt,
    }
    for name, payload in payloads.items():
        file_path = ready / name
        file_path.write_text(json.dumps(payload), encoding="utf-8")
        file_path.chmod(0o600)
    key_path = tmp_path / "origin-v2.key"
    key_path.write_text(
        encode_origin_key(origin_key) + "\n",
        encoding="utf-8",
    )
    key_path.chmod(0o600)
    return ready, key_path


def test_ready_module_verifies_exact_bundle_and_distinct_key_ids(tmp_path: Path) -> None:
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    ready, key_path = _bundle(tmp_path, now)

    result = verify_ready_bundle(
        ready_dir=ready,
        origin_key_path=key_path,
        expected_origin_key_id=ORIGIN_KEY_ID,
        observed_at=now + timedelta(seconds=3),
    )

    assert result["status"] == "execution_ready_origin_verified"
    assert result["transport_key_id"] == TRANSPORT_KEY_ID
    assert result["origin_key_id"] == ORIGIN_KEY_ID
    assert result["retry_allowed"] is False
    assert result["network_action_performed"] is False
    assert result["executor_invoked"] is False
    assert result["real_order_submitted"] is False


def test_ready_module_verifies_v2_source_truth_authorization(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    ready, key_path = _bundle_v2(tmp_path, now)

    result = verify_ready_bundle(
        ready_dir=ready,
        origin_key_path=key_path,
        expected_origin_key_id=ORIGIN_KEY_ID,
        observed_at=now + timedelta(seconds=4),
    )

    assert result["status"] == "execution_ready_source_truth_verified"
    assert result["source_truth_authorized"] is True
    assert result["source_truth_blockers"] == []
    assert len(result["project_state_sha256"]) == 64
    assert len(result["authorization_snapshot_sha256"]) == 64
    assert result["retry_allowed"] is False
    assert result["executor_invoked"] is False
    assert result["real_order_submitted"] is False


def test_ready_module_rejects_changed_v2_source_truth_mac(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    ready, key_path = _bundle_v2(
        tmp_path,
        now,
        alter_source_mac=True,
    )

    with pytest.raises(ReadyVerificationError, match="hmac mismatch"):
        verify_ready_bundle(
            ready_dir=ready,
            origin_key_path=key_path,
            expected_origin_key_id=ORIGIN_KEY_ID,
            observed_at=now + timedelta(seconds=4),
        )


def test_ready_module_rejects_forged_origin_hmac_with_expected_key_id(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    ready, key_path = _bundle(
        tmp_path,
        now,
        signing_key=bytes(range(64, 96)),
        verification_key=bytes(range(32, 64)),
    )

    with pytest.raises(ReadyVerificationError, match="hmac mismatch"):
        verify_ready_bundle(
            ready_dir=ready,
            origin_key_path=key_path,
            expected_origin_key_id=ORIGIN_KEY_ID,
            observed_at=now + timedelta(seconds=3),
        )


def test_ready_module_rejects_wrong_origin_key_and_receipt_key_ids(tmp_path: Path) -> None:
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    ready, key_path = _bundle(tmp_path, now)

    key_path.write_text(
        encode_origin_key(bytes(range(32))) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ReadyVerificationError, match="hmac mismatch"):
        verify_ready_bundle(
            ready_dir=ready,
            origin_key_path=key_path,
            expected_origin_key_id=ORIGIN_KEY_ID,
            observed_at=now + timedelta(seconds=3),
        )

    ready, key_path = _bundle(tmp_path / "second", now)
    receipt_path = ready / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["origin_key_id"] = "wrong-origin-key"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    receipt_path.chmod(0o600)
    with pytest.raises(ReadyVerificationError, match="origin key id mismatch"):
        verify_ready_bundle(
            ready_dir=ready,
            origin_key_path=key_path,
            expected_origin_key_id=ORIGIN_KEY_ID,
            observed_at=now + timedelta(seconds=3),
        )


def test_ready_module_rejects_mutation_and_weak_permissions(tmp_path: Path) -> None:
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    ready, key_path = _bundle(tmp_path, now)
    prepared_path = ready / "prepared.json"
    prepared = json.loads(prepared_path.read_text(encoding="utf-8"))
    prepared["request"]["limit_price"] = "0.71"
    prepared_path.write_text(json.dumps(prepared), encoding="utf-8")
    prepared_path.chmod(0o600)
    with pytest.raises(ReadyVerificationError, match="approved request changed"):
        verify_ready_bundle(
            ready_dir=ready,
            origin_key_path=key_path,
            expected_origin_key_id=ORIGIN_KEY_ID,
            observed_at=now + timedelta(seconds=3),
        )

    ready, key_path = _bundle(tmp_path / "permissions", now)
    (ready / "receipt.json").chmod(0o644)
    with pytest.raises(ReadyVerificationError, match="mode must be 0600"):
        verify_ready_bundle(
            ready_dir=ready,
            origin_key_path=key_path,
            expected_origin_key_id=ORIGIN_KEY_ID,
            observed_at=now + timedelta(seconds=3),
        )


def test_ready_module_has_no_network_wallet_or_execution_path() -> None:
    module_path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "bp_engine"
        / "execution"
        / "telegram_execution_ready.py"
    )
    text = module_path.read_text(encoding="utf-8")
    compile(text, str(module_path), "exec")
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

    assert (os.stat(module_path).st_mode & 0o111) == 0
