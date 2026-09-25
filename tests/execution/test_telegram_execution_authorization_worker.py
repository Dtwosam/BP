from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

from bp_engine.execution.telegram_approval import approval_record, new_pending
from bp_engine.execution.telegram_origin_attestation import (
    create_origin_attestation,
    encode_origin_key,
    payload_sha256,
)
from bp_engine.execution.telegram_source_truth_authorization import (
    create_source_truth_authorization,
)
from bp_engine.execution.telegram_transport import (
    create_authorized_transport_envelope,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "scripts"
    / "run_phase15_v3_telegram_execution_authorization_worker.py"
)
ORIGIN_KEY_ID = "phase15-telegram-origin-v1"
TRANSPORT_KEY_ID = "phase15-telegram-transport-v1"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "telegram_execution_authorization_worker_test",
        SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _prepared(now: datetime) -> dict[str, object]:
    return {
        "status": "prepared",
        "action": "submit",
        "intent_id": "live-intent-execution-auth-worker",
        "prediction_id": "prediction-execution-auth-worker",
        "paper_order_id": "paper-execution-auth-worker",
        "market_end_at": (now + timedelta(seconds=55)).isoformat(),
        "request": {
            "selected_side": "down",
            "limit_price": "0.72",
            "requested_shares": "6.81",
            "target_notional_usd": "5",
            "token_id": "token-execution-auth-worker",
            "action": "BUY",
        },
        "policy": {
            "policy_version": "v3-live-canary-v1",
            "max_submission_attempts": 1,
        },
    }


def _authorized_state() -> dict[str, object]:
    return {
        "source_of_truth_version": "synthetic-execution-auth-worker-v2",
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


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)


def _ready_bundle(
    tmp_path: Path,
    now: datetime,
) -> tuple[Path, Path]:
    prepared = _prepared(now)
    pending = new_pending(
        prepared,
        telegram_user_id=111,
        telegram_chat_id=111,
        created_at=now,
        nonce="execution-auth-worker-approval",
    )
    approval = approval_record(
        action="approve",
        pending=pending,
        callback_query_id="execution-auth-worker-callback",
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
    envelope = create_authorized_transport_envelope(
        prepared,
        approval=approval,
        origin_attestation=origin,
        source_truth_authorization=source_truth,
        key=bytes(range(32)),
        key_id=TRANSPORT_KEY_ID,
        created_at=now + timedelta(seconds=3),
        nonce="execution-auth-worker-transport",
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
        "origin_attestation_sha256": envelope[
            "origin_attestation_sha256"
        ],
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

    ready_root = tmp_path / "ready"
    ready_root.mkdir(mode=0o700)
    ready_dir = ready_root / "exact-order"
    ready_dir.mkdir(mode=0o700)
    for name, payload in (
        ("prepared.json", prepared),
        ("approval.json", envelope["approval"]),
        ("origin-attestation.json", origin),
        ("source-truth-authorization.json", source_truth),
        ("envelope.json", envelope),
        ("receipt.json", receipt),
    ):
        _write_json(ready_dir / name, payload)

    key_path = tmp_path / "origin.key"
    key_path.write_text(
        encode_origin_key(origin_key) + "\n",
        encoding="utf-8",
    )
    key_path.chmod(0o600)
    return ready_root, key_path


def _run_once(
    module: ModuleType,
    *,
    tmp_path: Path,
    ready_root: Path,
    key_path: Path,
    observed_at: datetime,
) -> list[dict[str, object]]:
    return module.authorize_pending_once(
        ready_root=ready_root,
        origin_key_path=key_path,
        expected_origin_key_id=ORIGIN_KEY_ID,
        dispatch_claim_dir=tmp_path / "dispatch-claims",
        handoff_root=tmp_path / "authorized",
        processed_dir=tmp_path / "processed",
        failure_dir=tmp_path / "failures",
        observed_at=observed_at,
    )


def test_execution_authorization_worker_materializes_one_shot_handoff(
    tmp_path: Path,
) -> None:
    module = _load()
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    ready_root, key_path = _ready_bundle(tmp_path, now)

    first = _run_once(
        module,
        tmp_path=tmp_path,
        ready_root=ready_root,
        key_path=key_path,
        observed_at=now + timedelta(seconds=4),
    )

    assert len(first) == 1
    result = first[0]
    assert result["status"] == "execution_authorized_handoff_ready"
    assert result["retry_allowed"] is False
    assert result["handoff_invoked"] is False
    assert result["executor_invoked"] is False
    assert result["real_order_submitted"] is False

    handoff_dir = Path(str(result["handoff_dir"]))
    assert handoff_dir.is_dir()
    assert (os.stat(handoff_dir).st_mode & 0o777) == 0o700
    for name in (
        "prepared.json",
        "approval.json",
        "ready-verification.json",
        "pre-execution.json",
        "dispatch-ticket.json",
        "dispatch-claim.json",
        "package-manifest.json",
        "receipt.json",
    ):
        path = handoff_dir / name
        assert path.is_file()
        assert (os.stat(path).st_mode & 0o777) == 0o600

    ticket = json.loads(
        (handoff_dir / "dispatch-ticket.json").read_text(encoding="utf-8")
    )
    receipt = json.loads(
        (handoff_dir / "receipt.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (handoff_dir / "package-manifest.json").read_text(encoding="utf-8")
    )
    assert ticket["expires_at"] == receipt["expires_at"]
    assert receipt["package_manifest_sha256"] == payload_sha256(manifest)
    assert result["package_manifest_sha256"] == receipt[
        "package_manifest_sha256"
    ]
    for name, metadata in manifest["files"].items():
        encoded = (handoff_dir / name).read_bytes()
        assert metadata["sha256"] == hashlib.sha256(encoded).hexdigest()
        assert metadata["size_bytes"] == len(encoded)
    processed_path = next((tmp_path / "processed").glob("*.json"))
    processed = json.loads(processed_path.read_text(encoding="utf-8"))
    assert processed["package_manifest_sha256"] == receipt[
        "package_manifest_sha256"
    ]
    assert receipt["handoff_invoked"] is False
    assert receipt["executor_invoked"] is False
    assert receipt["real_order_submitted"] is False
    assert list((tmp_path / "dispatch-claims").glob("*.json"))
    assert list((tmp_path / "processed").glob("*.json"))
    assert list((tmp_path / "failures").glob("*.json")) == []

    second = _run_once(
        module,
        tmp_path=tmp_path,
        ready_root=ready_root,
        key_path=key_path,
        observed_at=now + timedelta(seconds=5),
    )
    assert second == [
        {
            "status": "already_terminal",
            "ready_name": "exact-order",
            "retry_allowed": False,
            "handoff_invoked": False,
            "executor_invoked": False,
            "real_order_submitted": False,
        }
    ]


def test_execution_authorization_worker_consumed_claim_never_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load()
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    ready_root, key_path = _ready_bundle(tmp_path, now)

    def fail_materialization(**_: object) -> tuple[Path, str]:
        raise module.ExecutionAuthorizationWorkerError(
            "simulated-handoff-materialization-failure"
        )

    monkeypatch.setattr(module, "_materialize_handoff", fail_materialization)
    first = _run_once(
        module,
        tmp_path=tmp_path,
        ready_root=ready_root,
        key_path=key_path,
        observed_at=now + timedelta(seconds=4),
    )

    assert first[0]["status"] == "execution_authorization_failed_closed"
    assert first[0]["dispatch_claim_consumed"] is True
    assert first[0]["retry_allowed"] is False
    assert list((tmp_path / "dispatch-claims").glob("*.json"))
    assert list((tmp_path / "failures").glob("*.json"))

    second = _run_once(
        module,
        tmp_path=tmp_path,
        ready_root=ready_root,
        key_path=key_path,
        observed_at=now + timedelta(seconds=5),
    )
    assert second[0]["status"] == "already_terminal"


def test_execution_authorization_worker_never_publishes_partial_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load()
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    ready_root, key_path = _ready_bundle(tmp_path, now)
    original_write = module._write_private_json

    def fail_mid_package(
        path: Path,
        payload: dict[str, object],
    ) -> None:
        if path.parent.name.startswith(".") and path.name == "pre-execution.json":
            raise module.ExecutionAuthorizationWorkerError(
                "simulated-mid-package-write-failure"
            )
        original_write(path, payload)

    monkeypatch.setattr(module, "_write_private_json", fail_mid_package)
    result = _run_once(
        module,
        tmp_path=tmp_path,
        ready_root=ready_root,
        key_path=key_path,
        observed_at=now + timedelta(seconds=4),
    )

    assert result[0]["status"] == "execution_authorization_failed_closed"
    assert result[0]["dispatch_claim_consumed"] is True
    authorized = tmp_path / "authorized"
    assert list(authorized.iterdir()) == []
    assert list((tmp_path / "failures").glob("*.json"))


def test_execution_authorization_worker_rejects_unverified_package_before_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load()
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    ready_root, key_path = _ready_bundle(tmp_path, now)

    def fail_verify(**_: object) -> dict[str, object]:
        raise module.ExecutionPackageError(
            "simulated-package-verification-failure"
        )

    monkeypatch.setattr(
        module,
        "verify_execution_authorization_package",
        fail_verify,
    )
    result = _run_once(
        module,
        tmp_path=tmp_path,
        ready_root=ready_root,
        key_path=key_path,
        observed_at=now + timedelta(seconds=4),
    )

    assert result[0]["status"] == "execution_authorization_failed_closed"
    assert result[0]["dispatch_claim_consumed"] is True
    assert result[0]["retry_allowed"] is False
    assert list((tmp_path / "authorized").iterdir()) == []
    assert list((tmp_path / "processed").glob("*.json")) == []
    assert list((tmp_path / "processed").glob(".*.tmp")) == []
    assert list((tmp_path / "failures").glob("*.json"))


def test_execution_authorization_worker_rejects_legacy_ready_bundle(
    tmp_path: Path,
) -> None:
    module = _load()
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    ready_root, key_path = _ready_bundle(tmp_path, now)
    ready_dir = ready_root / "exact-order"
    source_truth_path = ready_dir / "source-truth-authorization.json"
    source_truth_path.unlink()
    envelope_path = ready_dir / "envelope.json"
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    envelope["schema_version"] = 1
    envelope.pop("source_truth_authorization", None)
    envelope.pop("source_truth_authorization_sha256", None)
    _write_json(envelope_path, envelope)

    result = _run_once(
        module,
        tmp_path=tmp_path,
        ready_root=ready_root,
        key_path=key_path,
        observed_at=now + timedelta(seconds=4),
    )

    assert result[0]["status"] == "execution_authorization_failed_closed"
    assert result[0]["dispatch_claim_consumed"] is False
    assert result[0]["retry_allowed"] is False
    assert list((tmp_path / "dispatch-claims").glob("*.json")) == []
    assert list((tmp_path / "authorized").glob("*")) == []


def test_execution_authorization_worker_source_has_no_handoff_or_order_path() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    compile(text, str(SCRIPT), "exec")
    for marker in (
        "verify_ready_bundle",
        "evaluate_signed_pre_execution_authorization",
        "create_dispatch_ticket",
        "claim_dispatch_ticket",
        "execution_authorized_handoff_ready",
        "handoff_invoked",
        "executor_invoked",
        "real_order_submitted",
    ):
        assert marker in text
    for forbidden in (
        "subprocess",
        "httpx",
        "urllib",
        "requests",
        "gcloud",
        "google.cloud",
        "post_order",
        "create_limit_order",
        "cancel_order",
        "phase15_v3_canary_arm",
        "phase15_v3_canary_executor",
        "phase15_v3_canary_telegram_handoff",
        "PHASE15_ACCEPT_REAL_MONEY",
        "POLYMARKET_PRIVATE_KEY=",
        "POLYMARKET_WALLET_ADDRESS=",
    ):
        assert forbidden not in text
