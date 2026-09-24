from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import stat
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bp_engine.execution.telegram_approval import (
    SUBMIT_SAFETY_FLOOR_SECONDS,
    ApprovalError,
    validate_approved_handoff,
)

ORIGIN_ATTESTATION_SCHEMA_VERSION = 1
ORIGIN_ATTESTATION_PURPOSE = "phase15-v3-telegram-origin-approval-v1"
ORIGIN_ATTESTATION_MAX_LIFETIME_SECONDS = 60
ORIGIN_ATTESTATION_FIELDS = frozenset(
    {
        "schema_version",
        "purpose",
        "key_id",
        "intent_id",
        "prediction_id",
        "paper_order_id",
        "request_sha256",
        "prepared_sha256",
        "approval_sha256",
        "approval_source_sha256",
        "approved_at",
        "attested_at",
        "expires_at",
        "hmac_sha256",
    }
)
EXECUTION_APPROVAL_FIELDS = (
    "schema_version",
    "status",
    "intent_id",
    "prediction_id",
    "paper_order_id",
    "request_sha256",
    "approved_at",
    "expires_at",
)


class OriginAttestationError(RuntimeError):
    pass


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise OriginAttestationError("datetime must be timezone-aware")
    return value.astimezone(UTC)


def _canonical(payload: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            dict(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError) as exc:
        raise OriginAttestationError("origin attestation payload is not canonicalizable") from exc


def payload_sha256(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def execution_approval(approval: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": int(approval.get("schema_version", 0)),
        "status": str(approval.get("status") or ""),
        "intent_id": str(approval.get("intent_id") or ""),
        "prediction_id": str(approval.get("prediction_id") or ""),
        "paper_order_id": str(approval.get("paper_order_id") or ""),
        "request_sha256": str(approval.get("request_sha256") or ""),
        "approved_at": str(approval.get("approved_at") or ""),
        "expires_at": str(approval.get("expires_at") or ""),
    }


def parse_origin_key(value: str) -> bytes:
    raw = value.strip()
    if not raw:
        raise OriginAttestationError("origin attestation key missing")
    try:
        padded = raw + "=" * (-len(raw) % 4)
        key = base64.b64decode(
            padded.encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, UnicodeEncodeError) as exc:
        raise OriginAttestationError(
            "origin attestation key is not valid base64url"
        ) from exc
    if len(key) != 32:
        raise OriginAttestationError(
            "origin attestation key must decode to exactly 32 bytes"
        )
    return key


def encode_origin_key(key: bytes) -> str:
    if len(key) != 32:
        raise OriginAttestationError("origin attestation key must be exactly 32 bytes")
    return base64.urlsafe_b64encode(key).decode("ascii").rstrip("=")


def generate_origin_key() -> str:
    return encode_origin_key(os.urandom(32))


def load_origin_key_file(path: Path) -> bytes:
    try:
        info = path.lstat()
    except OSError as exc:
        raise OriginAttestationError("origin attestation key file is not readable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise OriginAttestationError(
            "origin attestation key file must be a regular non-symlink file"
        )
    if stat.S_IMODE(info.st_mode) not in (0o600, 0o640):
        raise OriginAttestationError(
            "origin attestation key file mode must be 0600 or 0640"
        )
    if info.st_size <= 0 or info.st_size > 256:
        raise OriginAttestationError("origin attestation key file size invalid")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise OriginAttestationError("origin attestation key file is not readable") from exc
    if "\x00" in raw:
        raise OriginAttestationError("origin attestation key file contains NUL")
    stripped = raw.strip()
    if not stripped or any(line.strip() for line in raw.splitlines()[1:]):
        raise OriginAttestationError(
            "origin attestation key file must contain exactly one value"
        )
    return parse_origin_key(stripped)


def _key_id(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized.encode()) > 64:
        raise OriginAttestationError("origin attestation key id invalid")
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
    if any(ch not in allowed for ch in normalized):
        raise OriginAttestationError("origin attestation key id invalid")
    return normalized


def _mac(body: Mapping[str, Any], key: bytes) -> str:
    if len(key) != 32:
        raise OriginAttestationError("origin attestation key must be exactly 32 bytes")
    return hmac.new(key, _canonical(body), hashlib.sha256).hexdigest()


def create_origin_attestation(
    prepared: Mapping[str, Any],
    *,
    approval: Mapping[str, Any],
    key: bytes,
    key_id: str,
    attested_at: datetime,
) -> dict[str, Any]:
    attested = _utc(attested_at)
    normalized_key_id = _key_id(key_id)
    try:
        binding = validate_approved_handoff(
            prepared,
            approval=approval,
            observed_at=attested,
        )
    except ApprovalError as exc:
        raise OriginAttestationError(str(exc)) from exc

    approved_at = _utc(datetime.fromisoformat(binding["approved_at"]))
    approval_expires = _utc(datetime.fromisoformat(binding["expires_at"]))
    market_end = _utc(datetime.fromisoformat(binding["market_end_at"]))
    expires = min(
        attested + timedelta(seconds=ORIGIN_ATTESTATION_MAX_LIFETIME_SECONDS),
        approval_expires,
        market_end - timedelta(seconds=SUBMIT_SAFETY_FLOOR_SECONDS),
    )
    if expires <= attested:
        raise OriginAttestationError("origin attestation window already closed")

    sanitized = execution_approval(approval)
    body: dict[str, Any] = {
        "schema_version": ORIGIN_ATTESTATION_SCHEMA_VERSION,
        "purpose": ORIGIN_ATTESTATION_PURPOSE,
        "key_id": normalized_key_id,
        "intent_id": binding["intent_id"],
        "prediction_id": binding["prediction_id"],
        "paper_order_id": binding["paper_order_id"],
        "request_sha256": binding["request_sha256"],
        "prepared_sha256": payload_sha256(prepared),
        "approval_sha256": payload_sha256(sanitized),
        "approval_source_sha256": payload_sha256(approval),
        "approved_at": approved_at.isoformat(),
        "attested_at": attested.isoformat(),
        "expires_at": expires.isoformat(),
    }
    return {
        **body,
        "hmac_sha256": _mac(body, key),
    }


def verify_origin_attestation(
    attestation: Mapping[str, Any],
    *,
    prepared: Mapping[str, Any],
    approval: Mapping[str, Any],
    key: bytes,
    expected_key_id: str,
    observed_at: datetime,
) -> dict[str, Any]:
    observed = _utc(observed_at)
    if set(attestation) != ORIGIN_ATTESTATION_FIELDS:
        raise OriginAttestationError("origin attestation fields mismatch")
    if attestation.get("schema_version") != ORIGIN_ATTESTATION_SCHEMA_VERSION:
        raise OriginAttestationError("origin attestation schema mismatch")
    if attestation.get("purpose") != ORIGIN_ATTESTATION_PURPOSE:
        raise OriginAttestationError("origin attestation purpose mismatch")
    if str(attestation.get("key_id") or "") != _key_id(expected_key_id):
        raise OriginAttestationError("origin attestation key id mismatch")

    supplied_mac = str(attestation.get("hmac_sha256") or "")
    if re.fullmatch(r"[0-9a-f]{64}", supplied_mac) is None:
        raise OriginAttestationError("origin attestation hmac invalid")
    body = {
        name: value
        for name, value in attestation.items()
        if name != "hmac_sha256"
    }
    if not hmac.compare_digest(supplied_mac, _mac(body, key)):
        raise OriginAttestationError("origin attestation hmac mismatch")

    try:
        approved_at = _utc(datetime.fromisoformat(str(attestation["approved_at"])))
        attested_at = _utc(datetime.fromisoformat(str(attestation["attested_at"])))
        expires_at = _utc(datetime.fromisoformat(str(attestation["expires_at"])))
    except (KeyError, TypeError, ValueError) as exc:
        raise OriginAttestationError("origin attestation timestamps invalid") from exc
    if attested_at < approved_at:
        raise OriginAttestationError("origin attestation predates approval")
    if attested_at > observed:
        raise OriginAttestationError("origin attestation is from the future")
    if expires_at <= attested_at:
        raise OriginAttestationError("origin attestation expiry invalid")
    if (expires_at - attested_at).total_seconds() > ORIGIN_ATTESTATION_MAX_LIFETIME_SECONDS:
        raise OriginAttestationError("origin attestation lifetime exceeds maximum")
    if observed >= expires_at:
        raise OriginAttestationError("origin attestation expired")

    try:
        binding = validate_approved_handoff(
            prepared,
            approval=approval,
            observed_at=observed,
        )
    except ApprovalError as exc:
        raise OriginAttestationError(str(exc)) from exc

    sanitized = execution_approval(approval)
    expected = {
        "intent_id": str(binding["intent_id"]),
        "prediction_id": str(binding["prediction_id"]),
        "paper_order_id": str(binding["paper_order_id"]),
        "request_sha256": str(binding["request_sha256"]),
        "prepared_sha256": payload_sha256(prepared),
        "approval_sha256": payload_sha256(sanitized),
    }
    for name, value in expected.items():
        if str(attestation.get(name) or "") != value:
            raise OriginAttestationError(f"origin attestation {name} mismatch")

    source_hash = str(attestation.get("approval_source_sha256") or "")
    if re.fullmatch(r"[0-9a-f]{64}", source_hash) is None:
        raise OriginAttestationError("origin attestation approval_source_sha256 invalid")
    if str(attestation.get("approved_at") or "") != approved_at.isoformat():
        raise OriginAttestationError("origin attestation approved_at mismatch")
    if str(sanitized["approved_at"]) != approved_at.isoformat():
        raise OriginAttestationError("origin approval timestamp mismatch")

    return {
        "key_id": str(attestation["key_id"]),
        **expected,
        "approval_source_sha256": source_hash,
        "approved_at": approved_at.isoformat(),
        "attested_at": attested_at.isoformat(),
        "expires_at": expires_at.isoformat(),
    }
