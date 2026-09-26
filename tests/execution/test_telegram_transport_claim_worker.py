from __future__ import annotations

import importlib.util
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

from bp_engine.execution.telegram_approval import approval_record, new_pending
from bp_engine.execution.telegram_origin_attestation import create_origin_attestation
from bp_engine.execution.telegram_source_truth_authorization import (
    create_source_truth_authorization,
)
from bp_engine.execution.telegram_transport import (
    create_authorized_transport_envelope,
    create_transport_envelope,
    encode_transport_key,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run_phase15_v3_telegram_transport_claim_worker.py"
KEY_ID = "phase15-telegram-transport-v1"
ORIGIN_KEY_ID = "phase15-telegram-origin-v1"
ORIGIN_KEY = bytes(range(32, 64))


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("telegram_claim_worker_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _prepared(now: datetime) -> dict[str, object]:
    return {
        "status": "prepared",
        "action": "submit",
        "intent_id": "live-intent-claim-worker",
        "prediction_id": "prediction-claim-worker",
        "paper_order_id": "paper-claim-worker",
        "market_end_at": (now + timedelta(seconds=55)).isoformat(),
        "request": {
            "selected_side": "down",
            "limit_price": "0.72",
            "requested_shares": "6.81",
            "target_notional_usd": "5",
            "token_id": "token-claim-worker",
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
        key=ORIGIN_KEY,
        key_id=ORIGIN_KEY_ID,
        attested_at=now + timedelta(seconds=2),
    )


def _envelope(now: datetime) -> tuple[dict[str, object], bytes]:
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
        nonce="claim-worker-transport-nonce",
    )
    return envelope, key


def _authorized_state() -> dict[str, object]:
    return {
        "source_of_truth_version": "synthetic-claim-worker-v2",
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


def _authorized_envelope(now: datetime) -> tuple[dict[str, object], bytes]:
    prepared = _prepared(now)
    pending = new_pending(
        prepared,
        telegram_user_id=111,
        telegram_chat_id=111,
        created_at=now,
        nonce="claim-worker-v2-approval",
    )
    approval = approval_record(
        action="approve",
        pending=pending,
        callback_query_id="callback-v2",
        approved_at=now + timedelta(seconds=1),
    )
    origin = _origin_attestation(prepared, approval, now)
    source_truth = create_source_truth_authorization(
        _authorized_state(),
        prepared=prepared,
        approval=approval,
        origin_attestation=origin,
        key=ORIGIN_KEY,
        key_id=ORIGIN_KEY_ID,
        attested_at=now + timedelta(seconds=3),
    )
    key = bytes(range(32))
    envelope = create_authorized_transport_envelope(
        prepared,
        approval=approval,
        origin_attestation=origin,
        source_truth_authorization=source_truth,
        key=key,
        key_id=KEY_ID,
        created_at=now + timedelta(seconds=3),
        nonce="claim-worker-v2-transport",
    )
    return envelope, key


def _write_key(path: Path, key: bytes) -> None:
    path.write_text(encode_transport_key(key) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _write_envelope(path: Path, envelope: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_text(json.dumps(envelope), encoding="utf-8")
    path.chmod(0o600)


def test_claim_worker_consumes_inbox_once_and_materializes_ready(tmp_path: Path) -> None:
    worker = _load()
    now = datetime.now(UTC)
    envelope, key = _envelope(now)
    key_path = tmp_path / "transport.key"
    inbox = tmp_path / "inbox"
    envelope_path = inbox / "exact-order.json"
    _write_key(key_path, key)
    _write_envelope(envelope_path, envelope)

    claim_dir = tmp_path / "claims"
    ready_dir = tmp_path / "ready"
    processed_dir = tmp_path / "processed"
    failure_dir = tmp_path / "failures"

    first = worker.claim_pending_once(
        inbox_dir=inbox,
        claim_dir=claim_dir,
        ready_dir=ready_dir,
        processed_dir=processed_dir,
        failure_dir=failure_dir,
        key_path=key_path,
        expected_key_id=KEY_ID,
        observed_at=now + timedelta(seconds=3),
    )

    assert len(first) == 1
    result = first[0]
    assert result["status"] == "claimed_ready"
    assert result["retry_allowed"] is False
    assert result["executor_invoked"] is False
    assert result["real_order_submitted"] is False
    ready_path = Path(result["ready_path"])
    assert ready_path.is_dir()
    assert (os.stat(ready_path).st_mode & 0o777) == 0o750
    for name in (
        "prepared.json",
        "approval.json",
        "origin-attestation.json",
        "envelope.json",
        "receipt.json",
    ):
        assert (os.stat(ready_path / name).st_mode & 0o777) == 0o640
    ready_receipt = json.loads((ready_path / "receipt.json").read_text(encoding="utf-8"))
    assert ready_receipt["status"] == "claimed_ready"
    assert ready_receipt["executor_invoked"] is False
    assert ready_receipt["real_order_submitted"] is False
    assert (processed_dir / envelope_path.name).is_file()
    assert list(failure_dir.glob("*.json")) == []

    second = worker.claim_pending_once(
        inbox_dir=inbox,
        claim_dir=claim_dir,
        ready_dir=ready_dir,
        processed_dir=processed_dir,
        failure_dir=failure_dir,
        key_path=key_path,
        expected_key_id=KEY_ID,
        observed_at=now + timedelta(seconds=4),
    )
    assert second == []


def test_claim_worker_materializes_source_truth_for_v2(
    tmp_path: Path,
) -> None:
    worker = _load()
    now = datetime.now(UTC)
    envelope, key = _authorized_envelope(now)
    key_path = tmp_path / "transport.key"
    inbox = tmp_path / "inbox"
    envelope_path = inbox / "exact-order-v2.json"
    _write_key(key_path, key)
    _write_envelope(envelope_path, envelope)

    result = worker.claim_pending_once(
        inbox_dir=inbox,
        claim_dir=tmp_path / "claims",
        ready_dir=tmp_path / "ready",
        processed_dir=tmp_path / "processed",
        failure_dir=tmp_path / "failures",
        key_path=key_path,
        expected_key_id=KEY_ID,
        observed_at=now + timedelta(seconds=4),
    )[0]

    assert result["status"] == "claimed_ready"
    ready_path = Path(result["ready_path"])
    source_path = ready_path / "source-truth-authorization.json"
    assert source_path.is_file()
    assert (os.stat(source_path).st_mode & 0o777) == 0o640
    source = json.loads(source_path.read_text(encoding="utf-8"))
    receipt = json.loads(
        (ready_path / "receipt.json").read_text(encoding="utf-8")
    )
    assert source == envelope["source_truth_authorization"]
    assert receipt["source_truth_authorization_sha256"] == envelope[
        "source_truth_authorization_sha256"
    ]
    assert receipt["project_state_sha256"] == source["project_state_sha256"]
    assert receipt["authorization_snapshot_sha256"] == source[
        "authorization_snapshot_sha256"
    ]


def test_claim_worker_materialization_failure_consumes_claim_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _load()
    now = datetime.now(UTC)
    envelope, key = _envelope(now)
    key_path = tmp_path / "transport.key"
    inbox = tmp_path / "inbox"
    envelope_path = inbox / "exact-order.json"
    _write_key(key_path, key)
    _write_envelope(envelope_path, envelope)

    def fail_materialization(**_: object) -> Path:
        raise OSError("simulated-ready-disk-failure")

    monkeypatch.setattr(worker, "_materialize_ready", fail_materialization)

    claim_dir = tmp_path / "claims"
    first = worker.claim_pending_once(
        inbox_dir=inbox,
        claim_dir=claim_dir,
        ready_dir=tmp_path / "ready",
        processed_dir=tmp_path / "processed",
        failure_dir=tmp_path / "failures",
        key_path=key_path,
        expected_key_id=KEY_ID,
        observed_at=now + timedelta(seconds=3),
    )
    assert first[0]["status"] == "claim_consumed_materialization_failed"
    assert first[0]["retry_allowed"] is False
    assert list(claim_dir.glob("*.json"))
    failure = json.loads(
        Path(first[0]["failure_path"]).read_text(encoding="utf-8")
    )
    assert failure["claim_consumed"] is True
    assert failure["retry_allowed"] is False

    second = worker.claim_pending_once(
        inbox_dir=inbox,
        claim_dir=claim_dir,
        ready_dir=tmp_path / "ready",
        processed_dir=tmp_path / "processed",
        failure_dir=tmp_path / "failures",
        key_path=key_path,
        expected_key_id=KEY_ID,
        observed_at=now + timedelta(seconds=4),
    )
    assert second == []


def test_claim_worker_key_rotation_mismatch_waits_without_consuming(tmp_path: Path) -> None:
    worker = _load()
    now = datetime.now(UTC)
    envelope, key = _envelope(now)
    key_path = tmp_path / "transport.key"
    inbox = tmp_path / "inbox"
    _write_key(key_path, key)
    _write_envelope(inbox / "exact-order.json", envelope)

    result = worker.claim_pending_once(
        inbox_dir=inbox,
        claim_dir=tmp_path / "claims",
        ready_dir=tmp_path / "ready",
        processed_dir=tmp_path / "processed",
        failure_dir=tmp_path / "failures",
        key_path=key_path,
        expected_key_id="next-key-v2",
        observed_at=now + timedelta(seconds=3),
    )
    assert result == [
        {
            "status": "key_id_mismatch_retry_later",
            "inbox_name": "exact-order.json",
            "executor_invoked": False,
            "real_order_submitted": False,
        }
    ]
    assert list((tmp_path / "claims").glob("*.json")) == []
    assert list((tmp_path / "failures").glob("*.json")) == []


def test_claim_worker_expired_envelope_is_terminal_without_claim(tmp_path: Path) -> None:
    worker = _load()
    now = datetime.now(UTC)
    envelope, key = _envelope(now)
    key_path = tmp_path / "transport.key"
    inbox = tmp_path / "inbox"
    _write_key(key_path, key)
    _write_envelope(inbox / "exact-order.json", envelope)
    observed_at = datetime.fromisoformat(str(envelope["expires_at"]))

    result = worker.claim_pending_once(
        inbox_dir=inbox,
        claim_dir=tmp_path / "claims",
        ready_dir=tmp_path / "ready",
        processed_dir=tmp_path / "processed",
        failure_dir=tmp_path / "failures",
        key_path=key_path,
        expected_key_id=KEY_ID,
        observed_at=observed_at,
    )
    assert result[0]["status"] == "terminal_unclaimable_envelope"
    assert list((tmp_path / "claims").glob("*.json")) == []
    failure = json.loads(
        Path(result[0]["failure_path"]).read_text(encoding="utf-8")
    )
    assert failure["claim_consumed"] is False
    assert failure["retry_allowed"] is False


def test_claim_worker_source_has_no_network_or_execution_path() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    compile(text, str(SCRIPT), "exec")
    for marker in (
        "BP_TELEGRAM_TRANSPORT_CLAIM_WORKER_ENABLED",
        "claim_transport_envelope",
        "claimed_ready",
        "origin_key_id",
        "retry_allowed",
        "executor_invoked",
        "real_order_submitted",
    ):
        assert marker in text
    for forbidden in (
        "load_origin_key_file",
        "BP_TELEGRAM_ORIGIN_KEY_FILE",
        "BP_TELEGRAM_ORIGIN_KEY_ID",
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
        "POLYMARKET_PRIVATE_KEY=",
        "POLYMARKET_WALLET_ADDRESS=",
    ):
        assert forbidden not in text
