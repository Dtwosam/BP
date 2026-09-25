from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bp_engine.execution import telegram_privileged_handoff as handoff
from bp_engine.execution.telegram_execution_package import ExecutionPackageError

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run_phase15_v3_telegram_privileged_handoff_verify.py"


def _verified_package() -> dict[str, object]:
    return {
        "status": "execution_authorization_package_verified",
        "intent_id": "intent-1",
        "prediction_id": "prediction-1",
        "paper_order_id": "paper-1",
        "request_sha256": "1" * 64,
        "source_truth_sha256": "2" * 64,
        "authorization_report_sha256": "3" * 64,
        "dispatch_ticket_sha256": "4" * 64,
        "dispatch_claim_sha256": "5" * 64,
        "package_manifest_sha256": "6" * 64,
        "expires_at": "2026-09-25T12:00:00+00:00",
        "retry_allowed": False,
        "mutation_performed": False,
        "network_action_performed": False,
        "handoff_invoked": False,
        "executor_invoked": False,
        "real_order_submitted": False,
    }


def _executor(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "executor.py"
    path.write_text("#!/usr/bin/env python3\nprint('safe fixture')\n", encoding="utf-8")
    path.chmod(0o755)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return path, digest


def test_privileged_handoff_contract_is_read_only_and_exact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_dir = tmp_path / "package-id"
    package_dir.mkdir()
    processed = tmp_path / "processed.json"
    processed.write_text("{}\n", encoding="utf-8")
    executor, digest = _executor(tmp_path)
    calls: list[dict[str, object]] = []

    def fake_verify(**kwargs):
        calls.append(kwargs)
        return _verified_package()

    monkeypatch.setattr(
        handoff,
        "verify_execution_authorization_package",
        fake_verify,
    )

    result = handoff.verify_privileged_handoff_contract(
        package_dir=package_dir,
        processed_receipt_path=processed,
        executor_path=executor,
        expected_executor_sha256=digest,
        observed_at=datetime(2026, 9, 25, 11, 0, tzinfo=UTC),
        expected_owner_uid=os.getuid(),
    )

    assert len(calls) == 1
    assert calls[0]["package_dir"] == package_dir
    assert calls[0]["processed_receipt_path"] == processed
    assert calls[0]["expected_owner_uid"] == os.getuid()
    assert result["status"] == "privileged_handoff_contract_verified"
    assert result["intent_id"] == "intent-1"
    assert result["request_sha256"] == "1" * 64
    assert result["executor_sha256"] == digest
    assert result["package_identity"] == package_dir.name
    assert result["retry_allowed"] is False
    assert result["activation_manifest_created"] is False
    assert result["authorization_id_created"] is False
    assert result["kill_switch_mutated"] is False
    assert result["executor_payload_materialized"] is False
    assert result["mutation_performed"] is False
    assert result["network_action_performed"] is False
    assert result["handoff_invoked"] is False
    assert result["executor_invoked"] is False
    assert result["real_order_submitted"] is False


def test_privileged_handoff_contract_rejects_executor_hash_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, _ = _executor(tmp_path)
    monkeypatch.setattr(
        handoff,
        "verify_execution_authorization_package",
        lambda **_: _verified_package(),
    )

    with pytest.raises(
        handoff.PrivilegedHandoffContractError,
        match="executor SHA-256 mismatch",
    ):
        handoff.verify_privileged_handoff_contract(
            package_dir=tmp_path / "package-id",
            processed_receipt_path=tmp_path / "processed.json",
            executor_path=executor,
            expected_executor_sha256="0" * 64,
            observed_at=datetime(2026, 9, 25, 11, 0, tzinfo=UTC),
            expected_owner_uid=os.getuid(),
        )


def test_privileged_handoff_contract_rejects_group_writable_executor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, digest = _executor(tmp_path)
    executor.chmod(0o775)
    monkeypatch.setattr(
        handoff,
        "verify_execution_authorization_package",
        lambda **_: _verified_package(),
    )

    with pytest.raises(
        handoff.PrivilegedHandoffContractError,
        match="group/other writable",
    ):
        handoff.verify_privileged_handoff_contract(
            package_dir=tmp_path / "package-id",
            processed_receipt_path=tmp_path / "processed.json",
            executor_path=executor,
            expected_executor_sha256=digest,
            observed_at=datetime(2026, 9, 25, 11, 0, tzinfo=UTC),
            expected_owner_uid=os.getuid(),
        )


def test_privileged_handoff_contract_wraps_package_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, digest = _executor(tmp_path)

    def fail_verify(**_):
        raise ExecutionPackageError("package expired")

    monkeypatch.setattr(
        handoff,
        "verify_execution_authorization_package",
        fail_verify,
    )

    with pytest.raises(
        handoff.PrivilegedHandoffContractError,
        match="package expired",
    ):
        handoff.verify_privileged_handoff_contract(
            package_dir=tmp_path / "package-id",
            processed_receipt_path=tmp_path / "processed.json",
            executor_path=executor,
            expected_executor_sha256=digest,
            observed_at=datetime(2026, 9, 25, 11, 0, tzinfo=UTC),
            expected_owner_uid=os.getuid(),
        )


def test_handoff_contract_cli_has_no_execution_or_network_path() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    compile(source, str(SCRIPT), "exec")
    assert "verify_privileged_handoff_contract" in source
    for forbidden in (
        "subprocess",
        "urllib",
        "httpx",
        "requests",
        "google.cloud",
        "gcloud",
        "systemctl",
        "/etc/bp-canary",
        "post_order",
        "create_limit_order",
        "cancel_order",
        "executor.sh",
    ):
        assert forbidden not in source
