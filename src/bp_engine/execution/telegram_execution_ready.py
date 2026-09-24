from __future__ import annotations

import json
import stat
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from bp_engine.execution.telegram_origin_attestation import (
    OriginAttestationError,
    load_origin_key_file,
    payload_sha256,
    verify_origin_attestation,
)

MAX_READY_FILE_BYTES = 256 * 1024
READY_FILES = (
    "prepared.json",
    "approval.json",
    "origin-attestation.json",
    "envelope.json",
    "receipt.json",
)


class ReadyVerificationError(RuntimeError):
    pass


def _load_ready_file(ready_dir: Path, name: str) -> dict[str, Any]:
    path = ready_dir / name
    try:
        info = path.lstat()
    except OSError as exc:
        raise ReadyVerificationError(f"{name} is not readable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ReadyVerificationError(f"{name} must be a regular non-symlink file")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise ReadyVerificationError(f"{name} mode must be 0600")
    if info.st_size <= 0 or info.st_size > MAX_READY_FILE_BYTES:
        raise ReadyVerificationError(f"{name} size invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReadyVerificationError(f"{name} JSON invalid") from exc
    if not isinstance(payload, Mapping):
        raise ReadyVerificationError(f"{name} must contain a JSON object")
    return dict(payload)


def verify_ready_bundle(
    *,
    ready_dir: Path,
    origin_key_path: Path,
    expected_origin_key_id: str,
    observed_at: datetime,
) -> dict[str, Any]:
    try:
        info = ready_dir.lstat()
    except OSError as exc:
        raise ReadyVerificationError("ready directory is not accessible") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ReadyVerificationError("ready directory must be a non-symlink directory")
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise ReadyVerificationError("ready directory mode must be 0700")

    payloads = {name: _load_ready_file(ready_dir, name) for name in READY_FILES}
    prepared = payloads["prepared.json"]
    approval = payloads["approval.json"]
    origin_attestation = payloads["origin-attestation.json"]
    envelope = payloads["envelope.json"]
    receipt = payloads["receipt.json"]

    if receipt.get("status") != "claimed_ready":
        raise ReadyVerificationError("ready receipt status invalid")
    if receipt.get("retry_allowed") is not False:
        raise ReadyVerificationError("ready receipt retry policy invalid")
    if receipt.get("executor_invoked") is not False:
        raise ReadyVerificationError("ready receipt executor state invalid")
    if receipt.get("real_order_submitted") is not False:
        raise ReadyVerificationError("ready receipt money state invalid")

    envelope_origin = envelope.get("origin_attestation")
    if not isinstance(envelope_origin, Mapping):
        raise ReadyVerificationError("envelope origin attestation missing")
    if dict(envelope_origin) != origin_attestation:
        raise ReadyVerificationError("ready origin attestation differs from envelope")

    try:
        origin_key = load_origin_key_file(origin_key_path)
        verified = verify_origin_attestation(
            origin_attestation,
            prepared=prepared,
            approval=approval,
            key=origin_key,
            expected_key_id=expected_origin_key_id,
            observed_at=observed_at,
        )
    except OriginAttestationError as exc:
        raise ReadyVerificationError(str(exc)) from exc

    transport_key_id = str(envelope.get("key_id") or "")
    if not transport_key_id:
        raise ReadyVerificationError("ready envelope transport key id missing")
    if str(receipt.get("key_id") or "") != transport_key_id:
        raise ReadyVerificationError("ready receipt transport key id mismatch")

    origin_key_id = str(verified["key_id"])
    if str(receipt.get("origin_key_id") or "") != origin_key_id:
        raise ReadyVerificationError("ready receipt origin key id mismatch")

    expected = {
        "intent_id": verified["intent_id"],
        "prediction_id": verified["prediction_id"],
        "paper_order_id": verified["paper_order_id"],
        "request_sha256": verified["request_sha256"],
        "prepared_sha256": verified["prepared_sha256"],
        "approval_sha256": verified["approval_sha256"],
        "approval_source_sha256": verified["approval_source_sha256"],
        "origin_attestation_sha256": payload_sha256(origin_attestation),
    }
    for name, value in expected.items():
        if str(receipt.get(name) or "") != str(value):
            raise ReadyVerificationError(f"ready receipt {name} mismatch")
        if name in envelope and str(envelope.get(name) or "") != str(value):
            raise ReadyVerificationError(f"ready envelope {name} mismatch")

    return {
        "status": "execution_ready_origin_verified",
        "transport_key_id": transport_key_id,
        "origin_key_id": origin_key_id,
        **expected,
        "origin_attested_at": verified["attested_at"],
        "origin_expires_at": verified["expires_at"],
        "ready_dir": str(ready_dir),
        "retry_allowed": False,
        "network_action_performed": False,
        "executor_invoked": False,
        "real_order_submitted": False,
    }
