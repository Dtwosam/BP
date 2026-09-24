from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bp_engine.execution.telegram_approval import (
    SUBMIT_SAFETY_FLOOR_SECONDS,
    ApprovalError,
    validate_approved_handoff,
)

TRANSPORT_SCHEMA_VERSION = 1
TRANSPORT_PURPOSE = "phase15-v3-telegram-execution-v1"
TRANSPORT_MAX_LIFETIME_SECONDS = 15


class TransportError(RuntimeError):
    pass


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise TransportError("datetime must be timezone-aware")
    return value.astimezone(UTC)


def _canonical(payload: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            dict(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TransportError("transport payload is not canonicalizable") from exc


def payload_sha256(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def parse_transport_key(value: str) -> bytes:
    raw = value.strip()
    if not raw:
        raise TransportError("transport key missing")
    try:
        padded = raw + "=" * (-len(raw) % 4)
        key = base64.b64decode(
            padded.encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, UnicodeEncodeError) as exc:
        raise TransportError("transport key is not valid base64url") from exc
    if len(key) != 32:
        raise TransportError("transport key must decode to exactly 32 bytes")
    return key


def encode_transport_key(key: bytes) -> str:
    if len(key) != 32:
        raise TransportError("transport key must be exactly 32 bytes")
    return base64.urlsafe_b64encode(key).decode("ascii").rstrip("=")


def generate_transport_key() -> str:
    return encode_transport_key(os.urandom(32))


def _mac(body: Mapping[str, Any], key: bytes) -> str:
    if len(key) != 32:
        raise TransportError("transport key must be exactly 32 bytes")
    return hmac.new(key, _canonical(body), hashlib.sha256).hexdigest()


def create_transport_envelope(
    prepared: Mapping[str, Any],
    *,
    approval: Mapping[str, Any],
    key: bytes,
    created_at: datetime,
    nonce: str,
) -> dict[str, Any]:
    created = _utc(created_at)
    if not nonce or len(nonce.encode("utf-8")) > 80:
        raise TransportError("transport nonce invalid")

    try:
        binding = validate_approved_handoff(
            prepared,
            approval=approval,
            observed_at=created,
        )
    except ApprovalError as exc:
        raise TransportError(str(exc)) from exc

    approval_expires = datetime.fromisoformat(binding["expires_at"]).astimezone(UTC)
    market_end = datetime.fromisoformat(binding["market_end_at"]).astimezone(UTC)
    expires = min(
        created + timedelta(seconds=TRANSPORT_MAX_LIFETIME_SECONDS),
        approval_expires,
        market_end - timedelta(seconds=SUBMIT_SAFETY_FLOOR_SECONDS),
    )
    if expires <= created:
        raise TransportError("transport window already closed")

    prepared_copy = json.loads(_canonical(prepared).decode("utf-8"))
    approval_source_sha256 = payload_sha256(approval)
    approval_copy = {
        "schema_version": int(approval.get("schema_version", 0)),
        "status": str(approval.get("status") or ""),
        "intent_id": str(approval.get("intent_id") or ""),
        "prediction_id": str(approval.get("prediction_id") or ""),
        "paper_order_id": str(approval.get("paper_order_id") or ""),
        "request_sha256": str(approval.get("request_sha256") or ""),
        "approved_at": str(approval.get("approved_at") or ""),
        "expires_at": str(approval.get("expires_at") or ""),
    }
    body: dict[str, Any] = {
        "schema_version": TRANSPORT_SCHEMA_VERSION,
        "purpose": TRANSPORT_PURPOSE,
        "intent_id": binding["intent_id"],
        "prediction_id": binding["prediction_id"],
        "paper_order_id": binding["paper_order_id"],
        "request_sha256": binding["request_sha256"],
        "prepared_sha256": payload_sha256(prepared_copy),
        "approval_sha256": payload_sha256(approval_copy),
        "approval_source_sha256": approval_source_sha256,
        "transport_nonce": nonce,
        "created_at": created.isoformat(),
        "expires_at": expires.isoformat(),
        "prepared": prepared_copy,
        "approval": approval_copy,
    }
    return {
        **body,
        "hmac_sha256": _mac(body, key),
    }


def verify_transport_envelope(
    envelope: Mapping[str, Any],
    *,
    key: bytes,
    observed_at: datetime,
) -> dict[str, Any]:
    observed = _utc(observed_at)
    if envelope.get("schema_version") != TRANSPORT_SCHEMA_VERSION:
        raise TransportError("transport schema mismatch")
    if envelope.get("purpose") != TRANSPORT_PURPOSE:
        raise TransportError("transport purpose mismatch")

    supplied_mac = str(envelope.get("hmac_sha256") or "")
    if len(supplied_mac) != 64:
        raise TransportError("transport hmac invalid")
    body = {key_name: value for key_name, value in envelope.items() if key_name != "hmac_sha256"}
    expected_mac = _mac(body, key)
    if not hmac.compare_digest(supplied_mac, expected_mac):
        raise TransportError("transport hmac mismatch")

    prepared = envelope.get("prepared")
    approval = envelope.get("approval")
    if not isinstance(prepared, Mapping) or not isinstance(approval, Mapping):
        raise TransportError("transport payload missing")

    if str(envelope.get("prepared_sha256") or "") != payload_sha256(prepared):
        raise TransportError("prepared payload hash mismatch")
    if str(envelope.get("approval_sha256") or "") != payload_sha256(approval):
        raise TransportError("approval payload hash mismatch")

    try:
        created = _utc(datetime.fromisoformat(str(envelope["created_at"])))
        expires = _utc(datetime.fromisoformat(str(envelope["expires_at"])))
    except (KeyError, ValueError, TypeError) as exc:
        raise TransportError("transport timestamps invalid") from exc
    if created > observed:
        raise TransportError("transport created_at is in the future")
    if expires <= created:
        raise TransportError("transport expiry invalid")
    if (expires - created).total_seconds() > TRANSPORT_MAX_LIFETIME_SECONDS:
        raise TransportError("transport lifetime exceeds maximum")
    if observed >= expires:
        raise TransportError("transport envelope expired")

    try:
        binding = validate_approved_handoff(
            prepared,
            approval=approval,
            observed_at=observed,
        )
    except ApprovalError as exc:
        raise TransportError(str(exc)) from exc

    for field in ("intent_id", "prediction_id", "paper_order_id", "request_sha256"):
        if str(envelope.get(field) or "") != str(binding[field]):
            raise TransportError(f"transport {field} mismatch")
    if not str(envelope.get("transport_nonce") or ""):
        raise TransportError("transport nonce missing")

    return {
        "intent_id": binding["intent_id"],
        "prediction_id": binding["prediction_id"],
        "paper_order_id": binding["paper_order_id"],
        "request_sha256": binding["request_sha256"],
        "prepared_sha256": str(envelope["prepared_sha256"]),
        "approval_sha256": str(envelope["approval_sha256"]),
        "approval_source_sha256": str(envelope.get("approval_source_sha256") or ""),
        "transport_nonce": str(envelope["transport_nonce"]),
        "created_at": created.isoformat(),
        "expires_at": expires.isoformat(),
        "prepared": dict(prepared),
        "approval": dict(approval),
    }


def claim_transport_envelope(
    envelope: Mapping[str, Any],
    *,
    key: bytes,
    observed_at: datetime,
    state_dir: Path,
) -> dict[str, Any]:
    verified = verify_transport_envelope(
        envelope,
        key=key,
        observed_at=observed_at,
    )
    state_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(state_dir, 0o700)

    claim_key = hashlib.sha256(
        f"{verified['intent_id']}\0{verified['request_sha256']}".encode()
    ).hexdigest()
    claim_path = state_dir / f"{claim_key}.json"
    record = {
        "schema_version": 1,
        "status": "claimed",
        "intent_id": verified["intent_id"],
        "prediction_id": verified["prediction_id"],
        "paper_order_id": verified["paper_order_id"],
        "request_sha256": verified["request_sha256"],
        "prepared_sha256": verified["prepared_sha256"],
        "approval_sha256": verified["approval_sha256"],
        "approval_source_sha256": verified["approval_source_sha256"],
        "transport_nonce": verified["transport_nonce"],
        "claimed_at": _utc(observed_at).isoformat(),
        "retry_allowed": False,
    }
    encoded = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
    try:
        fd = os.open(
            claim_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError as exc:
        raise TransportError("transport envelope already claimed") from exc
    try:
        os.write(fd, encoded.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)

    return {
        **verified,
        "claim_id": claim_key,
        "claim_path": str(claim_path),
        "claim_sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    }
