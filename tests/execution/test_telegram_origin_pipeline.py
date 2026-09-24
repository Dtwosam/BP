from __future__ import annotations

import importlib.util
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

from bp_engine.execution.telegram_approval import approval_record, new_pending
from bp_engine.execution.telegram_origin_attestation import (
    OriginAttestationError,
    create_origin_attestation,
    encode_origin_key,
)
from bp_engine.execution.telegram_transport import (
    create_transport_envelope,
    encode_transport_key,
)

ROOT = Path(__file__).resolve().parents[2]
ATTEST_SCRIPT = ROOT / "scripts" / "run_phase15_v3_telegram_origin_attest.py"
CLAIM_WORKER = ROOT / "scripts" / "run_phase15_v3_telegram_transport_claim_worker.py"
VERIFY_SCRIPT = ROOT / "scripts" / "run_phase15_v3_telegram_execution_ready_verify.py"
ORIGIN_KEY_ID = "phase15-telegram-origin-v1"
TRANSPORT_KEY_ID = "phase15-telegram-transport-v1"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _prepared(now: datetime) -> dict[str, object]:
    return {
        "status": "prepared",
        "action": "submit",
        "intent_id": "live-intent-origin-pipeline",
        "prediction_id": "prediction-origin-pipeline",
        "paper_order_id": "paper-origin-pipeline",
        "market_end_at": (now + timedelta(seconds=55)).isoformat(),
        "request": {
            "selected_side": "down",
            "limit_price": "0.72",
            "requested_shares": "6.81",
            "target_notional_usd": "5",
            "token_id": "token-origin-pipeline",
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
        nonce="origin-pipeline-approval",
    )
    return approval_record(
        action="approve",
        pending=pending,
        callback_query_id="callback-id",
        approved_at=now + timedelta(seconds=1),
    )


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)


def _write_key(path: Path, value: str) -> None:
    path.write_text(value + "\n", encoding="utf-8")
    path.chmod(0o600)


def test_origin_attester_writes_once_without_forwarding_telegram_identity(
    tmp_path: Path,
) -> None:
    attester = _load(ATTEST_SCRIPT, "telegram_origin_attest_test")
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    prepared = _prepared(now)
    approval = _approval(prepared, now)
    prepared_path = tmp_path / "prepared.json"
    approval_path = tmp_path / "approval.json"
    origin_key_path = tmp_path / "origin.key"
    output_dir = tmp_path / "origin"
    output_dir.mkdir(mode=0o700)
    output_path = output_dir / "origin-attestation.json"
    _write_json(prepared_path, prepared)
    _write_json(approval_path, approval)
    _write_key(origin_key_path, encode_origin_key(bytes(range(32, 64))))

    result = attester.attest_origin(
        prepared_path=prepared_path,
        approval_path=approval_path,
        output_path=output_path,
        key_path=origin_key_path,
        key_id=ORIGIN_KEY_ID,
        attested_at=now + timedelta(seconds=2),
    )

    assert result["status"] == "origin_attested"
    assert result["network_action_performed"] is False
    assert result["executor_invoked"] is False
    assert result["real_order_submitted"] is False
    assert (os.stat(output_path).st_mode & 0o777) == 0o600
    serialized = output_path.read_text(encoding="utf-8")
    assert "telegram_user_id" not in serialized
    assert "telegram_chat_id" not in serialized
    assert "callback_query_id" not in serialized

    with pytest.raises(OriginAttestationError, match="already exists"):
        attester.attest_origin(
            prepared_path=prepared_path,
            approval_path=approval_path,
            output_path=output_path,
            key_path=origin_key_path,
            key_id=ORIGIN_KEY_ID,
            attested_at=now + timedelta(seconds=3),
        )


def test_full_safe_origin_transport_claim_verify_chain(tmp_path: Path) -> None:
    claim_worker = _load(CLAIM_WORKER, "telegram_origin_pipeline_claim")
    verifier = _load(VERIFY_SCRIPT, "telegram_origin_pipeline_verify")
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    prepared = _prepared(now)
    approval = _approval(prepared, now)
    origin_key = bytes(range(32, 64))
    transport_key = bytes(range(32))
    origin_attestation = create_origin_attestation(
        prepared,
        approval=approval,
        key=origin_key,
        key_id=ORIGIN_KEY_ID,
        attested_at=now + timedelta(seconds=2),
    )
    envelope = create_transport_envelope(
        prepared,
        approval=approval,
        origin_attestation=origin_attestation,
        key=transport_key,
        key_id=TRANSPORT_KEY_ID,
        created_at=now + timedelta(seconds=2),
        nonce="origin-pipeline-transport",
    )

    inbox = tmp_path / "inbox"
    envelope_path = inbox / "exact-order.json"
    transport_key_path = tmp_path / "transport.key"
    origin_key_path = tmp_path / "origin.key"
    _write_json(envelope_path, envelope)
    _write_key(transport_key_path, encode_transport_key(transport_key))
    _write_key(origin_key_path, encode_origin_key(origin_key))

    claimed = claim_worker.claim_pending_once(
        inbox_dir=inbox,
        claim_dir=tmp_path / "claims",
        ready_dir=tmp_path / "ready",
        processed_dir=tmp_path / "processed",
        failure_dir=tmp_path / "failures",
        key_path=transport_key_path,
        expected_key_id=TRANSPORT_KEY_ID,
        observed_at=now + timedelta(seconds=3),
    )
    assert claimed[0]["status"] == "claimed_ready"
    ready_dir = Path(claimed[0]["ready_path"])
    assert (ready_dir / "origin-attestation.json").is_file()

    verified = verifier.verify_ready_bundle(
        ready_dir=ready_dir,
        origin_key_path=origin_key_path,
        expected_origin_key_id=ORIGIN_KEY_ID,
        observed_at=now + timedelta(seconds=4),
    )
    assert verified["status"] == "execution_ready_origin_verified"
    assert verified["intent_id"] == prepared["intent_id"]
    assert verified["retry_allowed"] is False
    assert verified["network_action_performed"] is False
    assert verified["executor_invoked"] is False
    assert verified["real_order_submitted"] is False

    forged_origin_key = tmp_path / "forged-origin.key"
    _write_key(forged_origin_key, encode_origin_key(transport_key))
    with pytest.raises(verifier.ReadyVerificationError, match="hmac mismatch"):
        verifier.verify_ready_bundle(
            ready_dir=ready_dir,
            origin_key_path=forged_origin_key,
            expected_origin_key_id=ORIGIN_KEY_ID,
            observed_at=now + timedelta(seconds=4),
        )


def test_ready_verifier_rejects_post_claim_prepared_mutation(tmp_path: Path) -> None:
    claim_worker = _load(CLAIM_WORKER, "telegram_origin_mutation_claim")
    verifier = _load(VERIFY_SCRIPT, "telegram_origin_mutation_verify")
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    prepared = _prepared(now)
    approval = _approval(prepared, now)
    origin_key = bytes(range(32, 64))
    transport_key = bytes(range(32))
    origin_attestation = create_origin_attestation(
        prepared,
        approval=approval,
        key=origin_key,
        key_id=ORIGIN_KEY_ID,
        attested_at=now + timedelta(seconds=2),
    )
    envelope = create_transport_envelope(
        prepared,
        approval=approval,
        origin_attestation=origin_attestation,
        key=transport_key,
        key_id=TRANSPORT_KEY_ID,
        created_at=now + timedelta(seconds=2),
        nonce="origin-mutation-transport",
    )
    inbox = tmp_path / "inbox"
    _write_json(inbox / "exact-order.json", envelope)
    transport_key_path = tmp_path / "transport.key"
    origin_key_path = tmp_path / "origin.key"
    _write_key(transport_key_path, encode_transport_key(transport_key))
    _write_key(origin_key_path, encode_origin_key(origin_key))
    claimed = claim_worker.claim_pending_once(
        inbox_dir=inbox,
        claim_dir=tmp_path / "claims",
        ready_dir=tmp_path / "ready",
        processed_dir=tmp_path / "processed",
        failure_dir=tmp_path / "failures",
        key_path=transport_key_path,
        expected_key_id=TRANSPORT_KEY_ID,
        observed_at=now + timedelta(seconds=3),
    )
    ready_dir = Path(claimed[0]["ready_path"])
    mutated = json.loads((ready_dir / "prepared.json").read_text(encoding="utf-8"))
    mutated["request"]["limit_price"] = "0.71"
    (ready_dir / "prepared.json").write_text(json.dumps(mutated), encoding="utf-8")
    (ready_dir / "prepared.json").chmod(0o600)

    with pytest.raises(verifier.ReadyVerificationError, match="prepared_sha256 mismatch"):
        verifier.verify_ready_bundle(
            ready_dir=ready_dir,
            origin_key_path=origin_key_path,
            expected_origin_key_id=ORIGIN_KEY_ID,
            observed_at=now + timedelta(seconds=4),
        )


@pytest.mark.parametrize("path", [ATTEST_SCRIPT, VERIFY_SCRIPT])
def test_origin_boundary_scripts_have_no_network_or_order_path(path: Path) -> None:
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
        "phase15_v3_canary_arm",
        "phase15_v3_canary_executor",
        "PHASE15_ACCEPT_REAL_MONEY",
        "POLYMARKET_PRIVATE_KEY=",
        "POLYMARKET_WALLET_ADDRESS=",
    ):
        assert forbidden not in text
