from __future__ import annotations

import copy
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

from bp_engine.execution.telegram_approval import approval_record, new_pending
from bp_engine.execution.telegram_origin_attestation import create_origin_attestation
from bp_engine.execution.telegram_pubsub import envelope_attributes
from bp_engine.execution.telegram_transport import (
    create_transport_envelope,
    encode_transport_key,
)

ROOT = Path(__file__).resolve().parents[2]
STREAMING_SCRIPT = (
    ROOT / "scripts" / "run_phase15_v3_telegram_pubsub_streaming_receive.py"
)
KEY_ID = "phase15-telegram-transport-v1"
ORIGIN_KEY_ID = "phase15-telegram-origin-v1"


class FakeMessage:
    def __init__(
        self,
        *,
        envelope: dict[str, Any],
        message_id: str = "message-1",
        attributes: dict[str, str] | None = None,
    ) -> None:
        self.data = json.dumps(
            envelope,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        self.attributes = attributes or envelope_attributes(envelope)
        self.message_id = message_id
        self.ack_count = 0
        self.nack_count = 0

    def ack(self) -> None:
        self.ack_count += 1

    def nack(self) -> None:
        self.nack_count += 1


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "telegram_pubsub_streaming_test",
        STREAMING_SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _prepared(now: datetime) -> dict[str, object]:
    return {
        "status": "prepared",
        "action": "submit",
        "intent_id": "live-intent-stream",
        "prediction_id": "prediction-stream",
        "paper_order_id": "paper-stream",
        "market_end_at": (now + timedelta(seconds=55)).isoformat(),
        "request": {
            "selected_side": "down",
            "limit_price": "0.72",
            "requested_shares": "6.81",
            "target_notional_usd": "5",
            "token_id": "token-stream",
            "action": "BUY",
        },
        "policy": {
            "policy_version": "v3-live-canary-v1",
            "max_submission_attempts": 1,
        },
    }


def _origin_attestation(
    prepared: dict[str, object],
    approval: dict[str, object],
    now: datetime,
) -> dict[str, object]:
    return create_origin_attestation(
        prepared,
        approval=approval,
        key=bytes(range(32, 64)),
        key_id=ORIGIN_KEY_ID,
        attested_at=now + timedelta(seconds=2),
    )


def _envelope(now: datetime) -> tuple[dict[str, Any], bytes]:
    prepared = _prepared(now)
    pending = new_pending(
        prepared,
        telegram_user_id=111,
        telegram_chat_id=111,
        created_at=now,
        nonce="approval-nonce",
    )
    approval = approval_record(
        action="approve",
        pending=pending,
        callback_query_id="callback-id",
        approved_at=now + timedelta(seconds=1),
    )
    key = bytes(range(32))
    envelope = create_transport_envelope(
        prepared,
        approval=approval,
        origin_attestation=_origin_attestation(prepared, approval, now),
        key=key,
        key_id=KEY_ID,
        created_at=now + timedelta(seconds=2),
        nonce="streaming-transport-nonce",
    )
    return envelope, key


def _write_key(path: Path, key: bytes) -> None:
    path.write_text(encode_transport_key(key) + "\n", encoding="utf-8")
    path.chmod(0o600)


def test_streaming_receiver_persists_before_ack_and_dedupes(tmp_path: Path) -> None:
    module = _load()
    now = datetime.now(UTC)
    envelope, key = _envelope(now)
    key_path = tmp_path / "transport.key"
    _write_key(key_path, key)
    inbox = tmp_path / "inbox"
    rejected = tmp_path / "rejected"

    first_message = FakeMessage(envelope=envelope, message_id="message-1")
    first = module.process_message(
        first_message,
        key_path=key_path,
        expected_key_id=KEY_ID,
        inbox_dir=inbox,
        rejection_dir=rejected,
        observed_at=now + timedelta(seconds=3),
    )
    assert first["status"] == "received_acknowledged"
    assert first_message.ack_count == 1
    assert first_message.nack_count == 0
    stored = Path(first["envelope_path"])
    assert stored.is_file()
    assert json.loads(stored.read_text(encoding="utf-8")) == envelope

    duplicate_message = FakeMessage(envelope=envelope, message_id="message-2")
    duplicate = module.process_message(
        duplicate_message,
        key_path=key_path,
        expected_key_id=KEY_ID,
        inbox_dir=inbox,
        rejection_dir=rejected,
        observed_at=now + timedelta(seconds=4),
    )
    assert duplicate["status"] == "duplicate_acknowledged"
    assert duplicate_message.ack_count == 1
    assert duplicate_message.nack_count == 0


def test_streaming_receiver_durably_rejects_tampered_message_then_acks(
    tmp_path: Path,
) -> None:
    module = _load()
    now = datetime.now(UTC)
    envelope, key = _envelope(now)
    key_path = tmp_path / "transport.key"
    _write_key(key_path, key)
    tampered = copy.deepcopy(envelope)
    tampered["prepared"]["request"]["limit_price"] = "0.71"
    message = FakeMessage(envelope=tampered)

    result = module.process_message(
        message,
        key_path=key_path,
        expected_key_id=KEY_ID,
        inbox_dir=tmp_path / "inbox",
        rejection_dir=tmp_path / "rejected",
        observed_at=now + timedelta(seconds=3),
    )
    assert result["status"] == "invalid_acknowledged"
    assert message.ack_count == 1
    assert message.nack_count == 0
    rejection = json.loads(
        Path(result["rejection_path"]).read_text(encoding="utf-8")
    )
    assert rejection["status"] == "rejected"
    assert rejection["error_type"] == "TransportError"
    assert rejection["executor_invoked"] is False
    assert rejection["real_order_submitted"] is False


def test_streaming_receiver_nacks_local_key_failure_and_key_rotation_mismatch(
    tmp_path: Path,
) -> None:
    module = _load()
    now = datetime.now(UTC)
    envelope, key = _envelope(now)
    inbox = tmp_path / "inbox"
    rejected = tmp_path / "rejected"

    missing_key_message = FakeMessage(envelope=envelope)
    missing = module.process_message(
        missing_key_message,
        key_path=tmp_path / "missing.key",
        expected_key_id=KEY_ID,
        inbox_dir=inbox,
        rejection_dir=rejected,
        observed_at=now + timedelta(seconds=3),
    )
    assert missing["status"] == "local_key_unavailable_nacked"
    assert missing_key_message.ack_count == 0
    assert missing_key_message.nack_count == 1

    key_path = tmp_path / "transport.key"
    _write_key(key_path, key)
    mismatch_message = FakeMessage(envelope=envelope, message_id="message-mismatch")
    mismatch = module.process_message(
        mismatch_message,
        key_path=key_path,
        expected_key_id="next-key-v2",
        inbox_dir=inbox,
        rejection_dir=rejected,
        observed_at=now + timedelta(seconds=3),
    )
    assert mismatch["status"] == "key_id_mismatch_nacked"
    assert mismatch_message.ack_count == 0
    assert mismatch_message.nack_count == 1


def test_streaming_receiver_nacks_when_durable_inbox_is_unavailable(
    tmp_path: Path,
) -> None:
    module = _load()
    now = datetime.now(UTC)
    envelope, key = _envelope(now)
    key_path = tmp_path / "transport.key"
    _write_key(key_path, key)
    actual = tmp_path / "actual-inbox"
    actual.mkdir()
    inbox = tmp_path / "inbox"
    inbox.symlink_to(actual, target_is_directory=True)
    message = FakeMessage(envelope=envelope)

    result = module.process_message(
        message,
        key_path=key_path,
        expected_key_id=KEY_ID,
        inbox_dir=inbox,
        rejection_dir=tmp_path / "rejected",
        observed_at=now + timedelta(seconds=3),
    )
    assert result["status"] == "persistence_failed_nacked"
    assert message.ack_count == 0
    assert message.nack_count == 1
    assert list(actual.iterdir()) == []


def test_streaming_receiver_source_has_no_executor_or_order_path() -> None:
    text = STREAMING_SCRIPT.read_text(encoding="utf-8")
    compile(text, str(STREAMING_SCRIPT), "exec")
    for marker in (
        "compute_engine.Credentials",
        "SubscriberClient(credentials=credentials)",
        "subscriber.subscribe",
        "FlowControl",
        "max_messages=1",
        "message.ack()",
        "message.nack()",
        "BP_TELEGRAM_PUBSUB_STREAMING_RECEIVE_ENABLED",
        "executor_invoked",
        "real_order_submitted",
    ):
        assert marker in text
    for forbidden in (
        "gcloud",
        "post_order",
        "create_limit_order",
        "cancel_order",
        "phase15_v3_canary_arm",
        "phase15_v3_canary_executor",
        "PHASE15_ACCEPT_REAL_MONEY",
        "POLYMARKET_PRIVATE_KEY=",
        "POLYMARKET_WALLET_ADDRESS=",
    ):
        assert forbidden not in text
