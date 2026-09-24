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
from bp_engine.execution.telegram_transport import (
    TransportError,
    encode_transport_key,
    load_transport_key_file,
)

ROOT = Path(__file__).resolve().parents[2]
PACK_SCRIPT = ROOT / "scripts" / "run_phase15_v3_telegram_transport_pack.py"
CLAIM_SCRIPT = ROOT / "scripts" / "run_phase15_v3_telegram_transport_claim.py"


def _load_script(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _prepared(now: datetime) -> dict[str, object]:
    return {
        "status": "prepared",
        "action": "submit",
        "intent_id": "live-intent-transport",
        "prediction_id": "prediction-transport",
        "paper_order_id": "paper-transport",
        "market_end_at": (now + timedelta(seconds=55)).isoformat(),
        "request": {
            "selected_side": "down",
            "limit_price": "0.72",
            "requested_shares": "6.81",
            "target_notional_usd": "5",
            "token_id": "token-transport",
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
        callback_query_id="telegram-callback-id",
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
        key=bytes(range(32, 64)),
        key_id="phase15-telegram-origin-v1",
        attested_at=now + timedelta(seconds=2),
    )


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)


def _write_key(path: Path, key: bytes, mode: int = 0o600) -> None:
    path.write_text(encode_transport_key(key) + "\n", encoding="utf-8")
    path.chmod(mode)


def test_transport_key_file_requires_restricted_regular_non_symlink_file(
    tmp_path: Path,
) -> None:
    key = bytes(range(32))
    key_file = tmp_path / "transport.key"
    _write_key(key_file, key, 0o600)
    assert load_transport_key_file(key_file) == key

    key_file.chmod(0o640)
    assert load_transport_key_file(key_file) == key

    key_file.chmod(0o644)
    with pytest.raises(TransportError, match="mode must be 0600 or 0640"):
        load_transport_key_file(key_file)

    key_file.chmod(0o600)
    link = tmp_path / "transport-link.key"
    link.symlink_to(key_file)
    with pytest.raises(TransportError, match="regular non-symlink"):
        load_transport_key_file(link)


def test_pack_then_claim_materializes_exact_sanitized_one_shot_payload(
    tmp_path: Path,
) -> None:
    pack = _load_script(PACK_SCRIPT, "telegram_transport_pack")
    claim = _load_script(CLAIM_SCRIPT, "telegram_transport_claim")
    now = datetime.now(UTC)
    prepared = _prepared(now)
    approval = _approval(prepared, now)
    origin_attestation = _origin_attestation(prepared, approval, now)
    key = bytes(range(32))

    prepared_path = tmp_path / "prepared.json"
    approval_path = tmp_path / "approval.json"
    origin_attestation_path = tmp_path / "origin-attestation.json"
    key_path = tmp_path / "transport.key"
    envelope_path = tmp_path / "envelope.json"
    claims = tmp_path / "claims"
    materialized = tmp_path / "materialized"
    _write_json(prepared_path, prepared)
    _write_json(approval_path, approval)
    _write_json(origin_attestation_path, origin_attestation)
    _write_key(key_path, key)

    packed = pack.pack_transport(
        prepared_path=prepared_path,
        approval_path=approval_path,
        origin_attestation_path=origin_attestation_path,
        key_path=key_path,
        key_id="test-key-v1",
        output_path=envelope_path,
        created_at=now + timedelta(seconds=2),
        nonce="transport-nonce-cli",
    )
    assert packed["status"] == "packed"
    assert packed["network_action_performed"] is False
    assert packed["real_order_submitted"] is False
    assert (os.stat(envelope_path).st_mode & 0o777) == 0o600

    result = claim.claim_transport(
        envelope_path=envelope_path,
        key_path=key_path,
        expected_key_id="test-key-v1",
        claim_state_dir=claims,
        materialize_root=materialized,
        observed_at=now + timedelta(seconds=3),
    )
    assert result["status"] == "claimed_materialized"
    assert result["retry_allowed"] is False
    assert result["network_action_performed"] is False
    assert result["real_order_submitted"] is False

    output_dir = Path(result["materialized_dir"])
    assert (os.stat(output_dir).st_mode & 0o777) == 0o700
    for name in (
        "prepared.json",
        "approval.json",
        "origin-attestation.json",
        "envelope.json",
        "receipt.json",
    ):
        assert (os.stat(output_dir / name).st_mode & 0o777) == 0o600

    materialized_prepared = json.loads(
        (output_dir / "prepared.json").read_text(encoding="utf-8")
    )
    materialized_approval = json.loads(
        (output_dir / "approval.json").read_text(encoding="utf-8")
    )
    assert materialized_prepared == prepared
    assert materialized_approval["intent_id"] == prepared["intent_id"]
    assert "telegram_user_id" not in materialized_approval
    assert "telegram_chat_id" not in materialized_approval
    assert "callback_query_id" not in materialized_approval

    with pytest.raises(TransportError, match="already claimed"):
        claim.claim_transport(
            envelope_path=envelope_path,
            key_path=key_path,
            expected_key_id="test-key-v1",
            claim_state_dir=claims,
            materialize_root=materialized,
            observed_at=now + timedelta(seconds=4),
        )


def test_claim_consumes_transport_before_materialization_and_never_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = _load_script(PACK_SCRIPT, "telegram_transport_pack_failure")
    claim = _load_script(CLAIM_SCRIPT, "telegram_transport_claim_failure")
    now = datetime.now(UTC)
    prepared = _prepared(now)
    approval = _approval(prepared, now)
    origin_attestation = _origin_attestation(prepared, approval, now)
    key = bytes(range(32))

    prepared_path = tmp_path / "prepared.json"
    approval_path = tmp_path / "approval.json"
    origin_attestation_path = tmp_path / "origin-attestation.json"
    key_path = tmp_path / "transport.key"
    envelope_path = tmp_path / "envelope.json"
    claims = tmp_path / "claims"
    materialized = tmp_path / "materialized"
    _write_json(prepared_path, prepared)
    _write_json(approval_path, approval)
    _write_json(origin_attestation_path, origin_attestation)
    _write_key(key_path, key)

    pack.pack_transport(
        prepared_path=prepared_path,
        approval_path=approval_path,
        origin_attestation_path=origin_attestation_path,
        key_path=key_path,
        key_id="test-key-v1",
        output_path=envelope_path,
        created_at=now + timedelta(seconds=2),
        nonce="transport-nonce-failure",
    )

    def fail_materialization(**_: object) -> dict[str, object]:
        raise OSError("simulated disk failure")

    monkeypatch.setattr(claim, "_materialize_claim", fail_materialization)
    with pytest.raises(TransportError, match="claim consumed"):
        claim.claim_transport(
            envelope_path=envelope_path,
            key_path=key_path,
            expected_key_id="test-key-v1",
            claim_state_dir=claims,
            materialize_root=materialized,
            observed_at=now + timedelta(seconds=3),
        )

    with pytest.raises(TransportError, match="already claimed"):
        claim.claim_transport(
            envelope_path=envelope_path,
            key_path=key_path,
            expected_key_id="test-key-v1",
            claim_state_dir=claims,
            materialize_root=materialized,
            observed_at=now + timedelta(seconds=4),
        )


def test_transport_boundary_scripts_have_no_network_or_order_execution() -> None:
    for path in (PACK_SCRIPT, CLAIM_SCRIPT):
        text = path.read_text(encoding="utf-8")
        compile(text, str(path), "exec")
        for forbidden in (
            "subprocess",
            "httpx",
            "urllib",
            "requests",
            "gcloud",
            "post_order",
            "create_limit_order",
            "cancel_order",
            "PHASE15_ACCEPT_REAL_MONEY",
            "POLYMARKET_PRIVATE_KEY",
            "POLYMARKET_WALLET_ADDRESS",
        ):
            assert forbidden not in text


def test_pack_rejects_symlink_output_directory(tmp_path: Path) -> None:
    pack = _load_script(PACK_SCRIPT, "telegram_transport_pack_symlink")
    now = datetime.now(UTC)
    prepared = _prepared(now)
    approval = _approval(prepared, now)
    origin_attestation = _origin_attestation(prepared, approval, now)
    key = bytes(range(32))

    prepared_path = tmp_path / "prepared.json"
    approval_path = tmp_path / "approval.json"
    origin_attestation_path = tmp_path / "origin-attestation.json"
    key_path = tmp_path / "transport.key"
    actual = tmp_path / "actual-output"
    actual.mkdir()
    output_parent = tmp_path / "output"
    output_parent.symlink_to(actual, target_is_directory=True)
    _write_json(prepared_path, prepared)
    _write_json(approval_path, approval)
    _write_json(origin_attestation_path, origin_attestation)
    _write_key(key_path, key)

    with pytest.raises(TransportError, match="non-symlink directory"):
        pack.pack_transport(
            prepared_path=prepared_path,
            approval_path=approval_path,
            origin_attestation_path=origin_attestation_path,
            key_path=key_path,
            key_id="test-key-v1",
            output_path=output_parent / "envelope.json",
            created_at=now + timedelta(seconds=2),
            nonce="transport-nonce-pack-symlink",
        )


def test_claim_rejects_symlink_materialize_root_after_consuming_claim(
    tmp_path: Path,
) -> None:
    pack = _load_script(PACK_SCRIPT, "telegram_transport_pack_materialize_symlink")
    claim = _load_script(CLAIM_SCRIPT, "telegram_transport_claim_materialize_symlink")
    now = datetime.now(UTC)
    prepared = _prepared(now)
    approval = _approval(prepared, now)
    origin_attestation = _origin_attestation(prepared, approval, now)
    key = bytes(range(32))

    prepared_path = tmp_path / "prepared.json"
    approval_path = tmp_path / "approval.json"
    origin_attestation_path = tmp_path / "origin-attestation.json"
    key_path = tmp_path / "transport.key"
    envelope_path = tmp_path / "envelope.json"
    claims = tmp_path / "claims"
    actual = tmp_path / "actual-materialize"
    actual.mkdir()
    materialized = tmp_path / "materialized"
    materialized.symlink_to(actual, target_is_directory=True)
    _write_json(prepared_path, prepared)
    _write_json(approval_path, approval)
    _write_json(origin_attestation_path, origin_attestation)
    _write_key(key_path, key)

    pack.pack_transport(
        prepared_path=prepared_path,
        approval_path=approval_path,
        origin_attestation_path=origin_attestation_path,
        key_path=key_path,
        key_id="test-key-v1",
        output_path=envelope_path,
        created_at=now + timedelta(seconds=2),
        nonce="transport-nonce-materialize-symlink",
    )

    with pytest.raises(TransportError, match="claim consumed"):
        claim.claim_transport(
            envelope_path=envelope_path,
            key_path=key_path,
            expected_key_id="test-key-v1",
            claim_state_dir=claims,
            materialize_root=materialized,
            observed_at=now + timedelta(seconds=3),
        )

    with pytest.raises(TransportError, match="already claimed"):
        claim.claim_transport(
            envelope_path=envelope_path,
            key_path=key_path,
            expected_key_id="test-key-v1",
            claim_state_dir=claims,
            materialize_root=materialized,
            observed_at=now + timedelta(seconds=4),
        )
