from __future__ import annotations

import hashlib
import os
import re
import stat
from datetime import datetime
from pathlib import Path
from typing import Any

from bp_engine.execution.telegram_execution_package import (
    ExecutionPackageError,
    verify_execution_authorization_package,
)

HANDOFF_CONTRACT_SCHEMA_VERSION = 1
HANDOFF_CONTRACT_PURPOSE = "phase15-v3-privileged-handoff-contract-v1"
MAX_EXECUTOR_BYTES = 1024 * 1024


class PrivilegedHandoffContractError(RuntimeError):
    pass


def _verify_executor(
    path: Path,
    *,
    expected_sha256: str,
    expected_owner_uid: int,
) -> str:
    if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
        raise PrivilegedHandoffContractError("expected executor SHA-256 invalid")

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise PrivilegedHandoffContractError(
            "executor is not readable as a regular file"
        ) from exc

    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise PrivilegedHandoffContractError(
                "executor must be a regular non-symlink file"
            )
        if info.st_uid != expected_owner_uid:
            raise PrivilegedHandoffContractError("executor owner uid mismatch")
        if stat.S_IMODE(info.st_mode) & 0o022:
            raise PrivilegedHandoffContractError(
                "executor must not be group/other writable"
            )
        if info.st_size <= 0 or info.st_size > MAX_EXECUTOR_BYTES:
            raise PrivilegedHandoffContractError("executor size invalid")

        with os.fdopen(fd, "rb", closefd=False) as handle:
            data = handle.read(MAX_EXECUTOR_BYTES + 1)
        if len(data) != info.st_size:
            raise PrivilegedHandoffContractError(
                "executor changed while being verified"
            )
    finally:
        os.close(fd)

    observed_sha256 = hashlib.sha256(data).hexdigest()
    if observed_sha256 != expected_sha256:
        raise PrivilegedHandoffContractError("executor SHA-256 mismatch")
    return observed_sha256


def verify_privileged_handoff_contract(
    *,
    package_dir: Path,
    processed_receipt_path: Path,
    executor_path: Path,
    expected_executor_sha256: str,
    observed_at: datetime,
    expected_owner_uid: int = 0,
) -> dict[str, Any]:
    """Verify the exact read-only boundary before a future privileged handoff.

    This function intentionally cannot create an activation manifest, alter a kill
    switch, invoke the executor, access wallet material, or submit an order.
    """
    try:
        verified_package = verify_execution_authorization_package(
            package_dir=package_dir,
            processed_receipt_path=processed_receipt_path,
            observed_at=observed_at,
            expected_owner_uid=expected_owner_uid,
        )
    except ExecutionPackageError as exc:
        raise PrivilegedHandoffContractError(str(exc)) from exc

    executor_sha256 = _verify_executor(
        executor_path,
        expected_sha256=expected_executor_sha256,
        expected_owner_uid=expected_owner_uid,
    )

    return {
        "schema_version": HANDOFF_CONTRACT_SCHEMA_VERSION,
        "purpose": HANDOFF_CONTRACT_PURPOSE,
        "status": "privileged_handoff_contract_verified",
        "intent_id": str(verified_package["intent_id"]),
        "prediction_id": str(verified_package["prediction_id"]),
        "paper_order_id": str(verified_package["paper_order_id"]),
        "request_sha256": str(verified_package["request_sha256"]),
        "source_truth_sha256": str(verified_package["source_truth_sha256"]),
        "authorization_report_sha256": str(
            verified_package["authorization_report_sha256"]
        ),
        "dispatch_ticket_sha256": str(
            verified_package["dispatch_ticket_sha256"]
        ),
        "dispatch_claim_sha256": str(
            verified_package["dispatch_claim_sha256"]
        ),
        "package_manifest_sha256": str(
            verified_package["package_manifest_sha256"]
        ),
        "expires_at": str(verified_package["expires_at"]),
        "executor_path": str(executor_path),
        "executor_sha256": executor_sha256,
        "package_identity": package_dir.name,
        "retry_allowed": False,
        "activation_manifest_created": False,
        "authorization_id_created": False,
        "kill_switch_mutated": False,
        "executor_payload_materialized": False,
        "mutation_performed": False,
        "network_action_performed": False,
        "handoff_invoked": False,
        "executor_invoked": False,
        "real_order_submitted": False,
    }
