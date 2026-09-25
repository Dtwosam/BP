from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bp_engine.execution.telegram_dispatch_ticket import (
    claim_dispatch_ticket,
    create_dispatch_ticket,
)
from bp_engine.execution.telegram_execution_package import (
    ExecutionPackageError,
    verify_execution_authorization_package,
)
from bp_engine.execution.telegram_origin_attestation import payload_sha256
from bp_engine.execution.telegram_pre_execution import (
    evaluate_signed_pre_execution_authorization,
)

ROOT = Path(__file__).resolve().parents[2]
MODULE = (
    ROOT / "src" / "bp_engine" / "execution" / "telegram_execution_package.py"
)


def _write(path: Path, payload: dict[str, object]) -> None:
    encoded = (
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        + "\n"
    )
    path.write_text(encoded, encoding="utf-8")
    path.chmod(0o600)


def _build_package(
    tmp_path: Path,
    now: datetime,
) -> tuple[Path, Path, dict[str, object]]:
    prepared = {
        "status": "prepared",
        "action": "submit",
        "intent_id": "live-intent-package-verify",
        "prediction_id": "prediction-package-verify",
        "paper_order_id": "paper-package-verify",
        "request": {
            "selected_side": "down",
            "limit_price": "0.72",
            "requested_shares": "6.81",
            "target_notional_usd": "5",
            "token_id": "token-package-verify",
            "action": "BUY",
        },
    }
    request = prepared["request"]
    assert isinstance(request, dict)
    request_sha256 = payload_sha256(request)
    approval = {
        "schema_version": 1,
        "status": "approved",
        "action": "approve",
        "intent_id": prepared["intent_id"],
        "request_sha256": request_sha256,
        "approved_at": (now + timedelta(seconds=1)).isoformat(),
    }
    ready = {
        "schema_version": 1,
        "status": "execution_ready_source_truth_verified",
        "transport_key_id": "phase15-telegram-transport-v1",
        "origin_key_id": "phase15-telegram-origin-v1",
        "intent_id": prepared["intent_id"],
        "prediction_id": prepared["prediction_id"],
        "paper_order_id": prepared["paper_order_id"],
        "request_sha256": request_sha256,
        "prepared_sha256": payload_sha256(prepared),
        "approval_sha256": payload_sha256(approval),
        "approval_source_sha256": "2" * 64,
        "origin_attestation_sha256": "3" * 64,
        "origin_attested_at": now.isoformat(),
        "origin_expires_at": (now + timedelta(seconds=15)).isoformat(),
        "source_truth_authorization_sha256": "4" * 64,
        "project_state_sha256": "5" * 64,
        "authorization_snapshot_sha256": "6" * 64,
        "source_truth_attested_at": (
            now + timedelta(seconds=1)
        ).isoformat(),
        "source_truth_expires_at": (
            now + timedelta(seconds=14)
        ).isoformat(),
        "source_truth_authorized": True,
        "source_truth_blockers": [],
        "retry_allowed": False,
        "executor_invoked": False,
        "real_order_submitted": False,
    }
    pre_execution = evaluate_signed_pre_execution_authorization(
        ready_verification=ready,
    )
    ticket = create_dispatch_ticket(
        pre_execution,
        created_at=now + timedelta(seconds=2),
    )
    claim = claim_dispatch_ticket(
        ticket,
        pre_execution_report=pre_execution,
        ready_verification=ready,
        project_state=None,
        observed_at=now + timedelta(seconds=3),
        state_dir=tmp_path / "claims",
    )

    identity = hashlib.sha256(
        (
            f"{claim['intent_id']}\0"
            f"{claim['request_sha256']}"
        ).encode()
    ).hexdigest()
    package_dir = tmp_path / "authorized" / identity
    package_dir.mkdir(parents=True, mode=0o700)

    payloads = {
        "prepared.json": prepared,
        "approval.json": approval,
        "ready-verification.json": ready,
        "pre-execution.json": pre_execution,
        "dispatch-ticket.json": ticket,
        "dispatch-claim.json": claim,
    }
    manifest_files: dict[str, dict[str, object]] = {}
    for name, payload in payloads.items():
        path = package_dir / name
        _write(path, payload)
        encoded = path.read_bytes()
        manifest_files[name] = {
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "size_bytes": len(encoded),
        }

    manifest = {
        "schema_version": 1,
        "status": "execution_authorization_package_manifest",
        "intent_id": claim["intent_id"],
        "request_sha256": claim["request_sha256"],
        "expires_at": claim["expires_at"],
        "files": manifest_files,
        "retry_allowed": False,
        "handoff_invoked": False,
        "executor_invoked": False,
        "real_order_submitted": False,
    }
    manifest_sha256 = payload_sha256(manifest)
    _write(package_dir / "package-manifest.json", manifest)

    receipt = {
        "schema_version": 1,
        "status": "execution_authorized_handoff_ready",
        "intent_id": claim["intent_id"],
        "prediction_id": claim["prediction_id"],
        "paper_order_id": claim["paper_order_id"],
        "request_sha256": claim["request_sha256"],
        "prepared_sha256": claim["prepared_sha256"],
        "approval_sha256": claim["approval_sha256"],
        "approval_source_sha256": claim["approval_source_sha256"],
        "origin_attestation_sha256": claim["origin_attestation_sha256"],
        "source_truth_sha256": claim["source_truth_sha256"],
        "authorization_report_sha256": claim["authorization_report_sha256"],
        "dispatch_ticket_sha256": claim["dispatch_ticket_sha256"],
        "dispatch_claim_sha256": claim["claim_sha256"],
        "package_manifest_sha256": manifest_sha256,
        "expires_at": claim["expires_at"],
        "authorized_at": (now + timedelta(seconds=3)).isoformat(),
        "retry_allowed": False,
        "handoff_invoked": False,
        "executor_invoked": False,
        "real_order_submitted": False,
    }
    _write(package_dir / "receipt.json", receipt)

    processed = {
        "schema_version": 1,
        "status": "execution_authorized_handoff_ready",
        "ready_name": "exact-order",
        "intent_id": claim["intent_id"],
        "prediction_id": claim["prediction_id"],
        "paper_order_id": claim["paper_order_id"],
        "request_sha256": claim["request_sha256"],
        "prepared_sha256": claim["prepared_sha256"],
        "approval_sha256": claim["approval_sha256"],
        "approval_source_sha256": claim["approval_source_sha256"],
        "origin_attestation_sha256": claim["origin_attestation_sha256"],
        "source_truth_sha256": claim["source_truth_sha256"],
        "authorization_report_sha256": claim["authorization_report_sha256"],
        "dispatch_ticket_sha256": claim["dispatch_ticket_sha256"],
        "dispatch_claim_sha256": claim["claim_sha256"],
        "package_manifest_sha256": manifest_sha256,
        "expires_at": claim["expires_at"],
        "handoff_dir": str(package_dir),
        "retry_allowed": False,
        "handoff_invoked": False,
        "executor_invoked": False,
        "real_order_submitted": False,
    }
    processed_path = tmp_path / "processed.json"
    _write(processed_path, processed)
    return package_dir, processed_path, ready


def _verify(
    *,
    package_dir: Path,
    processed_path: Path,
    observed_at: datetime,
) -> dict[str, object]:
    return verify_execution_authorization_package(
        package_dir=package_dir,
        processed_receipt_path=processed_path,
        observed_at=observed_at,
        expected_owner_uid=os.getuid(),
    )


def test_execution_authorization_package_verifies_exact_chain(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    package_dir, processed_path, ready = _build_package(tmp_path, now)

    result = _verify(
        package_dir=package_dir,
        processed_path=processed_path,
        observed_at=now + timedelta(seconds=4),
    )

    assert result["status"] == "execution_authorization_package_verified"
    assert result["source_truth_sha256"] == ready["project_state_sha256"]
    assert result["retry_allowed"] is False
    assert result["mutation_performed"] is False
    assert result["network_action_performed"] is False
    assert result["handoff_invoked"] is False
    assert result["executor_invoked"] is False
    assert result["real_order_submitted"] is False


def test_execution_authorization_package_rejects_payload_tampering(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    package_dir, processed_path, _ = _build_package(tmp_path, now)
    path = package_dir / "prepared.json"
    prepared = json.loads(path.read_text(encoding="utf-8"))
    prepared["action"] = "changed"
    _write(path, prepared)

    with pytest.raises(ExecutionPackageError, match="file hash mismatch"):
        _verify(
            package_dir=package_dir,
            processed_path=processed_path,
            observed_at=now + timedelta(seconds=4),
        )


def test_execution_authorization_package_rejects_extra_file(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    package_dir, processed_path, _ = _build_package(tmp_path, now)
    _write(package_dir / "extra.json", {"unexpected": True})

    with pytest.raises(ExecutionPackageError, match="file set mismatch"):
        _verify(
            package_dir=package_dir,
            processed_path=processed_path,
            observed_at=now + timedelta(seconds=4),
        )


def test_execution_authorization_package_rejects_processed_receipt_drift(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    package_dir, processed_path, _ = _build_package(tmp_path, now)
    processed = json.loads(processed_path.read_text(encoding="utf-8"))
    processed["package_manifest_sha256"] = "9" * 64
    _write(processed_path, processed)

    with pytest.raises(
        ExecutionPackageError,
        match="processed receipt manifest hash mismatch",
    ):
        _verify(
            package_dir=package_dir,
            processed_path=processed_path,
            observed_at=now + timedelta(seconds=4),
        )


def test_execution_authorization_package_expires_with_signed_source_truth(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
    package_dir, processed_path, ready = _build_package(tmp_path, now)
    expires = datetime.fromisoformat(str(ready["source_truth_expires_at"]))

    with pytest.raises(ExecutionPackageError, match="expired"):
        _verify(
            package_dir=package_dir,
            processed_path=processed_path,
            observed_at=expires,
        )


def test_execution_authorization_package_module_has_no_execution_path() -> None:
    text = MODULE.read_text(encoding="utf-8")
    compile(text, str(MODULE), "exec")
    for marker in (
        "verify_pre_execution_snapshot",
        "verify_dispatch_ticket_against_report",
        "execution_authorization_package_verified",
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
        "PHASE15_ACCEPT_REAL_MONEY",
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_WALLET_ADDRESS",
    ):
        assert forbidden not in text
