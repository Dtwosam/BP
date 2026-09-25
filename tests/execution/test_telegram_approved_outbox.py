from __future__ import annotations

import importlib.util
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

from bp_engine.execution.telegram_approval import (
    approval_record,
    new_pending,
    request_sha256,
)
from bp_engine.execution.telegram_origin_attestation import encode_origin_key
from bp_engine.execution.telegram_transport import encode_transport_key

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run_phase15_v3_telegram_approved_outbox.py"
ORIGIN_KEY_ID = "phase15-telegram-origin-v1"
TRANSPORT_KEY_ID = "phase15-telegram-transport-v1"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("telegram_approved_outbox_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _prepared(now: datetime) -> dict[str, object]:
    return {
        "status": "prepared",
        "action": "submit",
        "intent_id": "live-intent-approved-outbox",
        "prediction_id": "prediction-approved-outbox",
        "paper_order_id": "paper-approved-outbox",
        "market_end_at": (now + timedelta(seconds=55)).isoformat(),
        "request": {
            "selected_side": "down",
            "limit_price": "0.72",
            "requested_shares": "6.81",
            "target_notional_usd": "5",
            "token_id": "token-approved-outbox",
            "action": "BUY",
        },
        "policy": {
            "policy_version": "v3-live-canary-v1",
            "max_submission_attempts": 1,
        },
    }


def _authorized_state() -> dict[str, object]:
    return {
        "source_of_truth_version": "synthetic-approved-outbox",
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


def _approval(prepared: dict[str, object], now: datetime) -> dict[str, object]:
    pending = new_pending(
        prepared,
        telegram_user_id=111,
        telegram_chat_id=111,
        created_at=now,
        nonce="approved-outbox-nonce",
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


def _inputs(tmp_path: Path, now: datetime) -> tuple[
    dict[str, object],
    Path,
    Path,
    Path,
    Path,
    Path,
]:
    prepared = _prepared(now)
    approval = _approval(prepared, now)
    state_dir = tmp_path / "approval-state"
    prepared_path = state_dir / "handoff-prepared.json"
    approval_path = state_dir / "approval.json"
    project_state_path = tmp_path / "PROJECT_STATE.json"
    origin_key_path = tmp_path / "origin.key"
    transport_key_path = tmp_path / "transport.key"
    _write_json(prepared_path, prepared)
    _write_json(approval_path, approval)
    project_state_path.write_text(
        json.dumps(_authorized_state()),
        encoding="utf-8",
    )
    project_state_path.chmod(0o644)
    _write_key(origin_key_path, encode_origin_key(bytes(range(32, 64))))
    _write_key(transport_key_path, encode_transport_key(bytes(range(32))))
    return (
        prepared,
        prepared_path,
        approval_path,
        project_state_path,
        origin_key_path,
        transport_key_path,
    )


def test_approved_outbox_stages_exact_origin_bound_envelope_once(tmp_path: Path) -> None:
    module = _load()
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    (
        prepared,
        prepared_path,
        approval_path,
        project_state_path,
        origin_key_path,
        transport_key_path,
    ) = _inputs(tmp_path, now)
    outbox = tmp_path / "outbox"

    result = module.stage_approved_outbox(
        prepared_path=prepared_path,
        approval_path=approval_path,
        project_state_path=project_state_path,
        origin_key_path=origin_key_path,
        origin_key_id=ORIGIN_KEY_ID,
        transport_key_path=transport_key_path,
        transport_key_id=TRANSPORT_KEY_ID,
        outbox_dir=outbox,
        expected_intent_id=str(prepared["intent_id"]),
        expected_request_sha256=request_sha256(prepared),
        observed_at=now + timedelta(seconds=2),
        nonce="approved-outbox-transport",
    )

    assert result["status"] == "approved_outbox_staged"
    assert result["origin_key_id"] == ORIGIN_KEY_ID
    assert result["transport_key_id"] == TRANSPORT_KEY_ID
    assert result["retry_allowed"] is False
    assert result["network_send_attempted"] is False
    assert result["executor_invoked"] is False
    assert result["real_order_submitted"] is False

    origin_path = Path(result["origin_attestation_path"])
    source_truth_path = Path(result["source_truth_authorization_path"])
    envelope_path = Path(result["envelope_path"])
    assert (os.stat(origin_path).st_mode & 0o777) == 0o600
    assert (os.stat(source_truth_path).st_mode & 0o777) == 0o600
    assert (os.stat(envelope_path).st_mode & 0o777) == 0o600
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    origin = json.loads(origin_path.read_text(encoding="utf-8"))
    source_truth = json.loads(source_truth_path.read_text(encoding="utf-8"))
    assert envelope["schema_version"] == 2
    assert envelope["origin_attestation"] == origin
    assert envelope["source_truth_authorization"] == source_truth
    assert source_truth["authorized"] is True
    assert source_truth["blockers"] == []
    assert envelope["intent_id"] == prepared["intent_id"]
    assert envelope["request_sha256"] == request_sha256(prepared)
    assert "telegram_user_id" not in envelope["approval"]
    assert "telegram_chat_id" not in envelope["approval"]
    assert "callback_query_id" not in envelope["approval"]
    assert "telegram_user_id" not in json.dumps(origin)

    with pytest.raises(module.ApprovedOutboxError, match="already exists"):
        module.stage_approved_outbox(
            prepared_path=prepared_path,
            approval_path=approval_path,
            project_state_path=project_state_path,
            origin_key_path=origin_key_path,
            origin_key_id=ORIGIN_KEY_ID,
            transport_key_path=transport_key_path,
            transport_key_id=TRANSPORT_KEY_ID,
            outbox_dir=outbox,
            expected_intent_id=str(prepared["intent_id"]),
            expected_request_sha256=request_sha256(prepared),
            observed_at=now + timedelta(seconds=3),
            nonce="approved-outbox-duplicate",
        )


def test_approved_outbox_rejects_listener_identity_mismatch_before_write(
    tmp_path: Path,
) -> None:
    module = _load()
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    (
        prepared,
        prepared_path,
        approval_path,
        project_state_path,
        origin_key_path,
        transport_key_path,
    ) = _inputs(tmp_path, now)
    outbox = tmp_path / "outbox"

    with pytest.raises(module.ApprovedOutboxError, match="intent id"):
        module.stage_approved_outbox(
            prepared_path=prepared_path,
            approval_path=approval_path,
            project_state_path=project_state_path,
            origin_key_path=origin_key_path,
            origin_key_id=ORIGIN_KEY_ID,
            transport_key_path=transport_key_path,
            transport_key_id=TRANSPORT_KEY_ID,
            outbox_dir=outbox,
            expected_intent_id="different-intent",
            expected_request_sha256=request_sha256(prepared),
            observed_at=now + timedelta(seconds=2),
            nonce="approved-outbox-mismatch",
        )
    assert not (approval_path.parent / "origin-attestation.json").exists()
    assert not outbox.exists()


def test_approved_outbox_rejects_same_key_material(tmp_path: Path) -> None:
    module = _load()
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    (
        prepared,
        prepared_path,
        approval_path,
        project_state_path,
        origin_key_path,
        transport_key_path,
    ) = _inputs(tmp_path, now)
    shared_key = bytes(range(32))
    _write_key(origin_key_path, encode_origin_key(shared_key))
    _write_key(transport_key_path, encode_transport_key(shared_key))

    with pytest.raises(module.ApprovedOutboxError, match="different material"):
        module.stage_approved_outbox(
            prepared_path=prepared_path,
            approval_path=approval_path,
            project_state_path=project_state_path,
            origin_key_path=origin_key_path,
            origin_key_id=ORIGIN_KEY_ID,
            transport_key_path=transport_key_path,
            transport_key_id=TRANSPORT_KEY_ID,
            outbox_dir=tmp_path / "outbox",
            expected_intent_id=str(prepared["intent_id"]),
            expected_request_sha256=request_sha256(prepared),
            observed_at=now + timedelta(seconds=2),
            nonce="approved-outbox-same-key",
        )


def test_approved_outbox_current_source_truth_stages_authorized_v2_once(
    tmp_path: Path,
) -> None:
    module = _load()
    now = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
    (
        prepared,
        prepared_path,
        approval_path,
        _project_state_path,
        origin_key_path,
        transport_key_path,
    ) = _inputs(tmp_path, now)
    outbox = tmp_path / "outbox"

    result = module.stage_approved_outbox(
        prepared_path=prepared_path,
        approval_path=approval_path,
        project_state_path=ROOT / "PROJECT_STATE.json",
        origin_key_path=origin_key_path,
        origin_key_id=ORIGIN_KEY_ID,
        transport_key_path=transport_key_path,
        transport_key_id=TRANSPORT_KEY_ID,
        outbox_dir=outbox,
        expected_intent_id=str(prepared["intent_id"]),
        expected_request_sha256=request_sha256(prepared),
        observed_at=now + timedelta(seconds=2),
        nonce="approved-outbox-current-authorized",
    )

    assert result["status"] == "approved_outbox_staged"
    assert result["retry_allowed"] is False
    assert result["network_send_attempted"] is False
    assert result["executor_invoked"] is False
    assert result["real_order_submitted"] is False
    assert Path(result["origin_attestation_path"]).is_file()
    assert Path(result["source_truth_authorization_path"]).is_file()
    assert Path(result["envelope_path"]).is_file()


def test_approved_outbox_source_has_no_network_or_order_path() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    compile(text, str(SCRIPT), "exec")
    for marker in (
        "BP_APPROVED_INTENT_ID",
        "BP_APPROVED_REQUEST_SHA256",
        "BP_TELEGRAM_PROJECT_STATE_FILE",
        "BP_TELEGRAM_ORIGIN_KEY_FILE",
        "BP_TELEGRAM_TRANSPORT_KEY_FILE",
        "network_send_attempted",
        "executor_invoked",
        "real_order_submitted",
    ):
        assert marker in text
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
