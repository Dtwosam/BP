from __future__ import annotations

import hashlib
import json
import re
import stat
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bp_engine.execution.telegram_dispatch_ticket import (
    DispatchTicketError,
    verify_dispatch_ticket_against_report,
)
from bp_engine.execution.telegram_origin_attestation import payload_sha256
from bp_engine.execution.telegram_pre_execution import (
    PreExecutionError,
    verify_pre_execution_snapshot,
)

PACKAGE_SCHEMA_VERSION = 1
PACKAGE_MANIFEST_STATUS = "execution_authorization_package_manifest"
PACKAGE_RECEIPT_STATUS = "execution_authorized_handoff_ready"
MAX_JSON_BYTES = 256 * 1024
PACKAGE_PAYLOAD_FILES = (
    "prepared.json",
    "approval.json",
    "ready-verification.json",
    "pre-execution.json",
    "dispatch-ticket.json",
    "dispatch-claim.json",
)
PACKAGE_FILES = frozenset(
    (*PACKAGE_PAYLOAD_FILES, "package-manifest.json", "receipt.json")
)


class ExecutionPackageError(RuntimeError):
    pass


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ExecutionPackageError(f"{label} is not readable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ExecutionPackageError(
            f"{label} must be a regular non-symlink file"
        )
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise ExecutionPackageError(f"{label} mode must be 0600")
    if info.st_size <= 0 or info.st_size > MAX_JSON_BYTES:
        raise ExecutionPackageError(f"{label} size invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExecutionPackageError(f"{label} JSON invalid") from exc
    if not isinstance(payload, Mapping):
        raise ExecutionPackageError(f"{label} must contain a JSON object")
    return dict(payload)


def _verify_safe_flags(payload: Mapping[str, Any], *, label: str) -> None:
    if payload.get("retry_allowed") is not False:
        raise ExecutionPackageError(f"{label} retry policy invalid")
    if payload.get("executor_invoked") is not False:
        raise ExecutionPackageError(f"{label} executor state invalid")
    if payload.get("real_order_submitted") is not False:
        raise ExecutionPackageError(f"{label} money state invalid")


def _claim_sha256(claim: Mapping[str, Any]) -> str:
    record = {
        name: value
        for name, value in claim.items()
        if name not in {"claim_id", "claim_path", "claim_sha256"}
    }
    encoded = (
        json.dumps(
            record,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _require_same(
    *payloads: Mapping[str, Any],
    fields: tuple[str, ...],
) -> None:
    for name in fields:
        values = {str(payload.get(name) or "") for payload in payloads}
        if "" in values or len(values) != 1:
            raise ExecutionPackageError(f"package {name} binding mismatch")


def verify_execution_authorization_package(
    *,
    package_dir: Path,
    processed_receipt_path: Path,
    observed_at: datetime,
) -> dict[str, Any]:
    observed = _utc(observed_at)
    try:
        directory_info = package_dir.lstat()
    except OSError as exc:
        raise ExecutionPackageError(
            "authorization package directory is not accessible"
        ) from exc
    if (
        stat.S_ISLNK(directory_info.st_mode)
        or not stat.S_ISDIR(directory_info.st_mode)
    ):
        raise ExecutionPackageError(
            "authorization package must be a non-symlink directory"
        )
    if stat.S_IMODE(directory_info.st_mode) != 0o700:
        raise ExecutionPackageError(
            "authorization package directory mode must be 0700"
        )

    names = {path.name for path in package_dir.iterdir()}
    if names != PACKAGE_FILES:
        raise ExecutionPackageError(
            "authorization package file set mismatch"
        )

    payloads = {
        name: _load_json(
            package_dir / name,
            label=f"authorization package {name}",
        )
        for name in PACKAGE_FILES
    }
    prepared = payloads["prepared.json"]
    approval = payloads["approval.json"]
    ready = payloads["ready-verification.json"]
    pre_execution = payloads["pre-execution.json"]
    ticket = payloads["dispatch-ticket.json"]
    claim = payloads["dispatch-claim.json"]
    manifest = payloads["package-manifest.json"]
    receipt = payloads["receipt.json"]
    processed = _load_json(
        processed_receipt_path,
        label="execution authorization processed receipt",
    )

    if manifest.get("schema_version") != PACKAGE_SCHEMA_VERSION:
        raise ExecutionPackageError("package manifest schema invalid")
    if manifest.get("status") != PACKAGE_MANIFEST_STATUS:
        raise ExecutionPackageError("package manifest status invalid")
    _verify_safe_flags(manifest, label="package manifest")
    if manifest.get("handoff_invoked") is not False:
        raise ExecutionPackageError("package manifest handoff state invalid")

    manifest_files = manifest.get("files")
    if not isinstance(manifest_files, Mapping):
        raise ExecutionPackageError("package manifest files invalid")
    if set(manifest_files) != set(PACKAGE_PAYLOAD_FILES):
        raise ExecutionPackageError("package manifest file set mismatch")
    for name in PACKAGE_PAYLOAD_FILES:
        metadata = manifest_files.get(name)
        if not isinstance(metadata, Mapping):
            raise ExecutionPackageError(
                f"package manifest metadata missing: {name}"
            )
        expected_sha256 = str(metadata.get("sha256") or "")
        if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
            raise ExecutionPackageError(
                f"package manifest hash invalid: {name}"
            )
        encoded = (package_dir / name).read_bytes()
        if hashlib.sha256(encoded).hexdigest() != expected_sha256:
            raise ExecutionPackageError(
                f"package file hash mismatch: {name}"
            )
        if metadata.get("size_bytes") != len(encoded):
            raise ExecutionPackageError(
                f"package file size mismatch: {name}"
            )

    manifest_sha256 = payload_sha256(manifest)
    if receipt.get("schema_version") != PACKAGE_SCHEMA_VERSION:
        raise ExecutionPackageError("package receipt schema invalid")
    if receipt.get("status") != PACKAGE_RECEIPT_STATUS:
        raise ExecutionPackageError("package receipt status invalid")
    _verify_safe_flags(receipt, label="package receipt")
    if receipt.get("handoff_invoked") is not False:
        raise ExecutionPackageError("package receipt handoff state invalid")
    if str(receipt.get("package_manifest_sha256") or "") != manifest_sha256:
        raise ExecutionPackageError("package receipt manifest hash mismatch")

    if processed.get("schema_version") != PACKAGE_SCHEMA_VERSION:
        raise ExecutionPackageError("processed receipt schema invalid")
    if processed.get("status") != PACKAGE_RECEIPT_STATUS:
        raise ExecutionPackageError("processed receipt status invalid")
    _verify_safe_flags(processed, label="processed receipt")
    if processed.get("handoff_invoked") is not False:
        raise ExecutionPackageError("processed receipt handoff state invalid")
    if str(processed.get("package_manifest_sha256") or "") != manifest_sha256:
        raise ExecutionPackageError(
            "processed receipt manifest hash mismatch"
        )
    if str(processed.get("handoff_dir") or "") != str(package_dir):
        raise ExecutionPackageError(
            "processed receipt handoff directory mismatch"
        )

    try:
        fresh_pre_execution = verify_pre_execution_snapshot(
            pre_execution,
            ready_verification=ready,
            project_state=None,
            require_authorized=True,
        )
        verified_ticket = verify_dispatch_ticket_against_report(
            ticket,
            pre_execution_report=fresh_pre_execution,
            ready_verification=ready,
            project_state=None,
            observed_at=observed,
        )
    except (PreExecutionError, DispatchTicketError) as exc:
        raise ExecutionPackageError(str(exc)) from exc

    if claim.get("schema_version") != PACKAGE_SCHEMA_VERSION:
        raise ExecutionPackageError("dispatch claim schema invalid")
    if claim.get("status") != "dispatch_claimed":
        raise ExecutionPackageError("dispatch claim status invalid")
    _verify_safe_flags(claim, label="dispatch claim")
    if str(claim.get("claim_sha256") or "") != _claim_sha256(claim):
        raise ExecutionPackageError("dispatch claim hash mismatch")

    _require_same(
        ready,
        fresh_pre_execution,
        verified_ticket,
        claim,
        receipt,
        processed,
        fields=(
            "intent_id",
            "prediction_id",
            "paper_order_id",
            "request_sha256",
            "prepared_sha256",
            "approval_sha256",
            "approval_source_sha256",
            "origin_attestation_sha256",
            "source_truth_sha256",
        ),
    )
    _require_same(
        fresh_pre_execution,
        verified_ticket,
        claim,
        receipt,
        processed,
        fields=("authorization_report_sha256",),
    )
    _require_same(
        verified_ticket,
        claim,
        receipt,
        processed,
        fields=("dispatch_ticket_sha256", "expires_at"),
    )
    if str(receipt.get("dispatch_claim_sha256") or "") != str(
        claim.get("claim_sha256") or ""
    ):
        raise ExecutionPackageError("package receipt dispatch claim mismatch")
    if str(processed.get("dispatch_claim_sha256") or "") != str(
        claim.get("claim_sha256") or ""
    ):
        raise ExecutionPackageError(
            "processed receipt dispatch claim mismatch"
        )

    if payload_sha256(prepared) != str(
        fresh_pre_execution["prepared_sha256"]
    ):
        raise ExecutionPackageError("prepared payload hash mismatch")
    if payload_sha256(approval) != str(
        fresh_pre_execution["approval_sha256"]
    ):
        raise ExecutionPackageError("approval payload hash mismatch")

    identity = hashlib.sha256(
        (
            f"{fresh_pre_execution['intent_id']}\0"
            f"{fresh_pre_execution['request_sha256']}"
        ).encode()
    ).hexdigest()
    if package_dir.name != identity:
        raise ExecutionPackageError(
            "authorization package directory identity mismatch"
        )

    return {
        "schema_version": 1,
        "status": "execution_authorization_package_verified",
        "intent_id": str(fresh_pre_execution["intent_id"]),
        "prediction_id": str(fresh_pre_execution["prediction_id"]),
        "paper_order_id": str(fresh_pre_execution["paper_order_id"]),
        "request_sha256": str(fresh_pre_execution["request_sha256"]),
        "source_truth_sha256": str(
            fresh_pre_execution["source_truth_sha256"]
        ),
        "authorization_report_sha256": str(
            fresh_pre_execution["authorization_report_sha256"]
        ),
        "dispatch_ticket_sha256": str(
            verified_ticket["dispatch_ticket_sha256"]
        ),
        "dispatch_claim_sha256": str(claim["claim_sha256"]),
        "package_manifest_sha256": manifest_sha256,
        "expires_at": str(verified_ticket["expires_at"]),
        "retry_allowed": False,
        "mutation_performed": False,
        "network_action_performed": False,
        "handoff_invoked": False,
        "executor_invoked": False,
        "real_order_submitted": False,
    }
