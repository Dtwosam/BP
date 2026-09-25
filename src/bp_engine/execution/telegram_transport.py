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
from bp_engine.execution.telegram_origin_attestation import (
    ORIGIN_ATTESTATION_FIELDS,
    ORIGIN_ATTESTATION_PURPOSE,
    ORIGIN_ATTESTATION_SCHEMA_VERSION,
)
from bp_engine.execution.telegram_pre_execution import (
    PROJECT_STATE_AUTHORIZATION_SNAPSHOT_FIELDS,
)
from bp_engine.execution.telegram_source_truth_authorization import (
    SOURCE_TRUTH_AUTHORIZATION_FIELDS,
    SOURCE_TRUTH_AUTHORIZATION_PURPOSE,
    SOURCE_TRUTH_AUTHORIZATION_SCHEMA_VERSION,
)

TRANSPORT_SCHEMA_VERSION = 1
AUTHORIZED_TRANSPORT_SCHEMA_VERSION = 2
TRANSPORT_PURPOSE = "phase15-v3-telegram-execution-v1"
TRANSPORT_MAX_LIFETIME_SECONDS = 15
TRANSPORT_ENVELOPE_FIELDS = frozenset(
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
        "origin_attestation_sha256",
        "origin_attestation",
        "transport_nonce",
        "created_at",
        "expires_at",
        "prepared",
        "approval",
        "hmac_sha256",
    }
)
AUTHORIZED_TRANSPORT_ENVELOPE_FIELDS = frozenset(
    set(TRANSPORT_ENVELOPE_FIELDS)
    | {
        "source_truth_authorization_sha256",
        "source_truth_authorization",
    }
)
TRANSPORT_APPROVAL_FIELDS = frozenset(
    {
        "schema_version",
        "status",
        "intent_id",
        "prediction_id",
        "paper_order_id",
        "request_sha256",
        "approved_at",
        "expires_at",
    }
)


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


def load_transport_key_file(path: Path) -> bytes:
    try:
        info = path.lstat()
    except OSError as exc:
        raise TransportError("transport key file is not readable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise TransportError("transport key file must be a regular non-symlink file")
    mode = stat.S_IMODE(info.st_mode)
    if mode not in (0o600, 0o640):
        raise TransportError("transport key file mode must be 0600 or 0640")
    if info.st_size <= 0 or info.st_size > 256:
        raise TransportError("transport key file size invalid")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TransportError("transport key file is not readable") from exc
    if "\x00" in raw:
        raise TransportError("transport key file contains NUL")
    stripped = raw.strip()
    if not stripped or any(line.strip() for line in raw.splitlines()[1:]):
        raise TransportError("transport key file must contain exactly one value")
    return parse_transport_key(stripped)


def _mac(body: Mapping[str, Any], key: bytes) -> str:
    if len(key) != 32:
        raise TransportError("transport key must be exactly 32 bytes")
    return hmac.new(key, _canonical(body), hashlib.sha256).hexdigest()


def _key_id(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized.encode("utf-8")) > 64:
        raise TransportError("transport key id invalid")
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
    if any(ch not in allowed for ch in normalized):
        raise TransportError("transport key id invalid")
    return normalized


def _validate_origin_attestation_binding(
    origin_attestation: Mapping[str, Any],
    *,
    prepared_sha256: str,
    approval_sha256: str,
    approval_source_sha256: str,
    intent_id: str,
    prediction_id: str,
    paper_order_id: str,
    request_sha256: str,
) -> tuple[dict[str, Any], datetime, datetime]:
    if set(origin_attestation) != ORIGIN_ATTESTATION_FIELDS:
        raise TransportError("origin attestation fields mismatch")
    if origin_attestation.get("schema_version") != ORIGIN_ATTESTATION_SCHEMA_VERSION:
        raise TransportError("origin attestation schema mismatch")
    if origin_attestation.get("purpose") != ORIGIN_ATTESTATION_PURPOSE:
        raise TransportError("origin attestation purpose mismatch")
    origin_key_id = str(origin_attestation.get("key_id") or "").strip()
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
    if (
        not origin_key_id
        or len(origin_key_id.encode()) > 64
        or any(ch not in allowed for ch in origin_key_id)
    ):
        raise TransportError("origin attestation key id invalid")

    expected = {
        "intent_id": intent_id,
        "prediction_id": prediction_id,
        "paper_order_id": paper_order_id,
        "request_sha256": request_sha256,
        "prepared_sha256": prepared_sha256,
        "approval_sha256": approval_sha256,
        "approval_source_sha256": approval_source_sha256,
    }
    for name, value in expected.items():
        if str(origin_attestation.get(name) or "") != value:
            raise TransportError(f"origin attestation {name} mismatch")

    supplied_mac = str(origin_attestation.get("hmac_sha256") or "")
    if re.fullmatch(r"[0-9a-f]{64}", supplied_mac) is None:
        raise TransportError("origin attestation hmac invalid")
    try:
        attested_at = _utc(
            datetime.fromisoformat(str(origin_attestation["attested_at"]))
        )
        expires_at = _utc(
            datetime.fromisoformat(str(origin_attestation["expires_at"]))
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise TransportError("origin attestation timestamps invalid") from exc
    if expires_at <= attested_at:
        raise TransportError("origin attestation expiry invalid")

    copied = json.loads(_canonical(origin_attestation).decode())
    return copied, attested_at, expires_at


def _validate_source_truth_authorization_binding(
    authorization: Mapping[str, Any],
    *,
    origin_attestation_sha256: str,
    origin_key_id: str,
    origin_attested_at: datetime,
    origin_expires_at: datetime,
    prepared_sha256: str,
    approval_sha256: str,
    approval_source_sha256: str,
    intent_id: str,
    prediction_id: str,
    paper_order_id: str,
    request_sha256: str,
) -> tuple[dict[str, Any], datetime, datetime]:
    if set(authorization) != SOURCE_TRUTH_AUTHORIZATION_FIELDS:
        raise TransportError("source truth authorization fields mismatch")
    if (
        authorization.get("schema_version")
        != SOURCE_TRUTH_AUTHORIZATION_SCHEMA_VERSION
    ):
        raise TransportError("source truth authorization schema mismatch")
    if authorization.get("purpose") != SOURCE_TRUTH_AUTHORIZATION_PURPOSE:
        raise TransportError("source truth authorization purpose mismatch")
    if str(authorization.get("key_id") or "") != origin_key_id:
        raise TransportError("source truth authorization key id mismatch")
    if authorization.get("authorized") is not True:
        raise TransportError("source truth authorization is blocked")
    if authorization.get("blockers") != []:
        raise TransportError("source truth authorization contains blockers")

    snapshot = authorization.get("authorization_snapshot")
    if not isinstance(snapshot, Mapping):
        raise TransportError("source truth authorization snapshot missing")
    if set(snapshot) != PROJECT_STATE_AUTHORIZATION_SNAPSHOT_FIELDS:
        raise TransportError("source truth authorization snapshot fields mismatch")
    if (
        str(authorization.get("authorization_snapshot_sha256") or "")
        != payload_sha256(snapshot)
    ):
        raise TransportError("source truth authorization snapshot hash mismatch")

    expected = {
        "intent_id": intent_id,
        "prediction_id": prediction_id,
        "paper_order_id": paper_order_id,
        "request_sha256": request_sha256,
        "prepared_sha256": prepared_sha256,
        "approval_sha256": approval_sha256,
        "approval_source_sha256": approval_source_sha256,
        "origin_attestation_sha256": origin_attestation_sha256,
        "origin_attested_at": origin_attested_at.isoformat(),
    }
    for name, value in expected.items():
        if str(authorization.get(name) or "") != value:
            raise TransportError(f"source truth authorization {name} mismatch")

    for name in (
        "project_state_sha256",
        "authorization_snapshot_sha256",
        "request_sha256",
        "prepared_sha256",
        "approval_sha256",
        "approval_source_sha256",
        "origin_attestation_sha256",
        "hmac_sha256",
    ):
        value = str(authorization.get(name) or "")
        if re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise TransportError(f"source truth authorization {name} invalid")

    try:
        attested_at = _utc(
            datetime.fromisoformat(str(authorization["attested_at"]))
        )
        expires_at = _utc(
            datetime.fromisoformat(str(authorization["expires_at"]))
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise TransportError(
            "source truth authorization timestamps invalid"
        ) from exc
    if attested_at < origin_attested_at:
        raise TransportError(
            "source truth authorization predates origin attestation"
        )
    if expires_at <= attested_at:
        raise TransportError("source truth authorization expiry invalid")
    if expires_at > origin_expires_at:
        raise TransportError(
            "source truth authorization outlives origin attestation"
        )

    copied = json.loads(_canonical(authorization).decode())
    return copied, attested_at, expires_at


def create_transport_envelope(
    prepared: Mapping[str, Any],
    *,
    approval: Mapping[str, Any],
    origin_attestation: Mapping[str, Any],
    key: bytes,
    key_id: str,
    created_at: datetime,
    nonce: str,
) -> dict[str, Any]:
    created = _utc(created_at)
    normalized_key_id = _key_id(key_id)
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

    prepared_copy = json.loads(_canonical(prepared).decode())
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
    prepared_sha256 = payload_sha256(prepared_copy)
    approval_sha256 = payload_sha256(approval_copy)
    origin_copy, origin_attested_at, origin_expires_at = _validate_origin_attestation_binding(
        origin_attestation,
        prepared_sha256=prepared_sha256,
        approval_sha256=approval_sha256,
        approval_source_sha256=approval_source_sha256,
        intent_id=str(binding["intent_id"]),
        prediction_id=str(binding["prediction_id"]),
        paper_order_id=str(binding["paper_order_id"]),
        request_sha256=str(binding["request_sha256"]),
    )
    if origin_attested_at > created:
        raise TransportError("transport predates origin attestation")
    if created >= origin_expires_at:
        raise TransportError("origin attestation expired before transport")
    expires = min(expires, origin_expires_at)
    if expires <= created:
        raise TransportError("transport window already closed")

    body: dict[str, Any] = {
        "schema_version": TRANSPORT_SCHEMA_VERSION,
        "purpose": TRANSPORT_PURPOSE,
        "key_id": normalized_key_id,
        "intent_id": binding["intent_id"],
        "prediction_id": binding["prediction_id"],
        "paper_order_id": binding["paper_order_id"],
        "request_sha256": binding["request_sha256"],
        "prepared_sha256": prepared_sha256,
        "approval_sha256": approval_sha256,
        "approval_source_sha256": approval_source_sha256,
        "origin_attestation_sha256": payload_sha256(origin_copy),
        "origin_attestation": origin_copy,
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


def create_authorized_transport_envelope(
    prepared: Mapping[str, Any],
    *,
    approval: Mapping[str, Any],
    origin_attestation: Mapping[str, Any],
    source_truth_authorization: Mapping[str, Any],
    key: bytes,
    key_id: str,
    created_at: datetime,
    nonce: str,
) -> dict[str, Any]:
    base = create_transport_envelope(
        prepared,
        approval=approval,
        origin_attestation=origin_attestation,
        key=key,
        key_id=key_id,
        created_at=created_at,
        nonce=nonce,
    )
    created = _utc(created_at)
    origin_attested_at = _utc(
        datetime.fromisoformat(str(origin_attestation["attested_at"]))
    )
    origin_expires_at = _utc(
        datetime.fromisoformat(str(origin_attestation["expires_at"]))
    )
    source_copy, source_attested_at, source_expires_at = (
        _validate_source_truth_authorization_binding(
            source_truth_authorization,
            origin_attestation_sha256=str(base["origin_attestation_sha256"]),
            origin_key_id=str(origin_attestation["key_id"]),
            origin_attested_at=origin_attested_at,
            origin_expires_at=origin_expires_at,
            prepared_sha256=str(base["prepared_sha256"]),
            approval_sha256=str(base["approval_sha256"]),
            approval_source_sha256=str(base["approval_source_sha256"]),
            intent_id=str(base["intent_id"]),
            prediction_id=str(base["prediction_id"]),
            paper_order_id=str(base["paper_order_id"]),
            request_sha256=str(base["request_sha256"]),
        )
    )
    if source_attested_at > created:
        raise TransportError("transport predates source truth authorization")
    if created >= source_expires_at:
        raise TransportError("source truth authorization expired before transport")

    body = {
        name: value
        for name, value in base.items()
        if name != "hmac_sha256"
    }
    body["schema_version"] = AUTHORIZED_TRANSPORT_SCHEMA_VERSION
    body["source_truth_authorization_sha256"] = payload_sha256(source_copy)
    body["source_truth_authorization"] = source_copy
    base_expires = _utc(datetime.fromisoformat(str(body["expires_at"])))
    body["expires_at"] = min(base_expires, source_expires_at).isoformat()
    return {
        **body,
        "hmac_sha256": _mac(body, key),
    }


def verify_transport_envelope(
    envelope: Mapping[str, Any],
    *,
    key: bytes,
    expected_key_id: str,
    observed_at: datetime,
) -> dict[str, Any]:
    observed = _utc(observed_at)
    schema_version = envelope.get("schema_version")
    if schema_version == TRANSPORT_SCHEMA_VERSION:
        expected_fields = TRANSPORT_ENVELOPE_FIELDS
    elif schema_version == AUTHORIZED_TRANSPORT_SCHEMA_VERSION:
        expected_fields = AUTHORIZED_TRANSPORT_ENVELOPE_FIELDS
    else:
        raise TransportError("transport schema mismatch")
    if set(envelope) != expected_fields:
        raise TransportError("transport envelope fields mismatch")
    if envelope.get("purpose") != TRANSPORT_PURPOSE:
        raise TransportError("transport purpose mismatch")
    if str(envelope.get("key_id") or "") != _key_id(expected_key_id):
        raise TransportError("transport key id mismatch")

    supplied_mac = str(envelope.get("hmac_sha256") or "")
    if len(supplied_mac) != 64:
        raise TransportError("transport hmac invalid")
    body = {key_name: value for key_name, value in envelope.items() if key_name != "hmac_sha256"}
    expected_mac = _mac(body, key)
    if not hmac.compare_digest(supplied_mac, expected_mac):
        raise TransportError("transport hmac mismatch")

    prepared = envelope.get("prepared")
    approval = envelope.get("approval")
    origin_attestation = envelope.get("origin_attestation")
    source_truth_authorization = envelope.get("source_truth_authorization")
    if (
        not isinstance(prepared, Mapping)
        or not isinstance(approval, Mapping)
        or not isinstance(origin_attestation, Mapping)
    ):
        raise TransportError("transport payload missing")
    if (
        schema_version == AUTHORIZED_TRANSPORT_SCHEMA_VERSION
        and not isinstance(source_truth_authorization, Mapping)
    ):
        raise TransportError("source truth authorization payload missing")
    if set(approval) != TRANSPORT_APPROVAL_FIELDS:
        raise TransportError("transport approval fields mismatch")

    if str(envelope.get("prepared_sha256") or "") != payload_sha256(prepared):
        raise TransportError("prepared payload hash mismatch")
    if str(envelope.get("approval_sha256") or "") != payload_sha256(approval):
        raise TransportError("approval payload hash mismatch")
    if (
        str(envelope.get("origin_attestation_sha256") or "")
        != payload_sha256(origin_attestation)
    ):
        raise TransportError("origin attestation hash mismatch")
    if (
        schema_version == AUTHORIZED_TRANSPORT_SCHEMA_VERSION
        and str(envelope.get("source_truth_authorization_sha256") or "")
        != payload_sha256(source_truth_authorization)
    ):
        raise TransportError("source truth authorization hash mismatch")

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

    origin_copy, origin_attested_at, origin_expires_at = _validate_origin_attestation_binding(
        origin_attestation,
        prepared_sha256=str(envelope["prepared_sha256"]),
        approval_sha256=str(envelope["approval_sha256"]),
        approval_source_sha256=str(envelope["approval_source_sha256"]),
        intent_id=str(binding["intent_id"]),
        prediction_id=str(binding["prediction_id"]),
        paper_order_id=str(binding["paper_order_id"]),
        request_sha256=str(binding["request_sha256"]),
    )
    if origin_attested_at > created:
        raise TransportError("transport predates origin attestation")
    if expires > origin_expires_at:
        raise TransportError("transport outlives origin attestation")
    if observed >= origin_expires_at:
        raise TransportError("origin attestation expired")

    source_copy: dict[str, Any] | None = None
    if schema_version == AUTHORIZED_TRANSPORT_SCHEMA_VERSION:
        assert isinstance(source_truth_authorization, Mapping)
        source_copy, source_attested_at, source_expires_at = (
            _validate_source_truth_authorization_binding(
                source_truth_authorization,
                origin_attestation_sha256=str(
                    envelope["origin_attestation_sha256"]
                ),
                origin_key_id=str(origin_copy["key_id"]),
                origin_attested_at=origin_attested_at,
                origin_expires_at=origin_expires_at,
                prepared_sha256=str(envelope["prepared_sha256"]),
                approval_sha256=str(envelope["approval_sha256"]),
                approval_source_sha256=str(
                    envelope["approval_source_sha256"]
                ),
                intent_id=str(binding["intent_id"]),
                prediction_id=str(binding["prediction_id"]),
                paper_order_id=str(binding["paper_order_id"]),
                request_sha256=str(binding["request_sha256"]),
            )
        )
        if source_attested_at > created:
            raise TransportError(
                "transport predates source truth authorization"
            )
        if expires > source_expires_at:
            raise TransportError(
                "transport outlives source truth authorization"
            )
        if observed >= source_expires_at:
            raise TransportError("source truth authorization expired")

    transport_nonce = str(envelope.get("transport_nonce") or "")
    if not transport_nonce or len(transport_nonce.encode()) > 80:
        raise TransportError("transport nonce invalid")
    for name in (
        "request_sha256",
        "prepared_sha256",
        "approval_sha256",
        "approval_source_sha256",
        "origin_attestation_sha256",
    ):
        value = str(envelope.get(name) or "")
        if re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise TransportError(f"transport {name} invalid")
    if schema_version == AUTHORIZED_TRANSPORT_SCHEMA_VERSION:
        value = str(envelope.get("source_truth_authorization_sha256") or "")
        if re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise TransportError(
                "transport source_truth_authorization_sha256 invalid"
            )

    return {
        "key_id": str(envelope["key_id"]),
        "intent_id": binding["intent_id"],
        "prediction_id": binding["prediction_id"],
        "paper_order_id": binding["paper_order_id"],
        "request_sha256": binding["request_sha256"],
        "prepared_sha256": str(envelope["prepared_sha256"]),
        "approval_sha256": str(envelope["approval_sha256"]),
        "approval_source_sha256": str(envelope.get("approval_source_sha256") or ""),
        "origin_attestation_sha256": str(
            envelope.get("origin_attestation_sha256") or ""
        ),
        "origin_attestation": origin_copy,
        "origin_key_id": str(origin_copy["key_id"]),
        "transport_nonce": transport_nonce,
        "created_at": created.isoformat(),
        "expires_at": expires.isoformat(),
        "prepared": dict(prepared),
        "approval": dict(approval),
        **(
            {
                "source_truth_authorization_sha256": str(
                    envelope["source_truth_authorization_sha256"]
                ),
                "source_truth_authorization": source_copy,
            }
            if source_copy is not None
            else {}
        ),
    }


def _ensure_private_directory(path: Path) -> None:
    try:
        path.mkdir(parents=True, mode=0o700)
    except FileExistsError:
        pass
    try:
        info = path.lstat()
    except OSError as exc:
        raise TransportError("transport state directory is not accessible") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise TransportError("transport state directory must be a non-symlink directory")
    os.chmod(path, 0o700)


def claim_transport_envelope(
    envelope: Mapping[str, Any],
    *,
    key: bytes,
    expected_key_id: str,
    observed_at: datetime,
    state_dir: Path,
) -> dict[str, Any]:
    verified = verify_transport_envelope(
        envelope,
        key=key,
        expected_key_id=expected_key_id,
        observed_at=observed_at,
    )
    _ensure_private_directory(state_dir)

    claim_key = hashlib.sha256(
        f"{verified['intent_id']}\0{verified['request_sha256']}".encode()
    ).hexdigest()
    claim_path = state_dir / f"{claim_key}.json"
    record = {
        "schema_version": 1,
        "status": "claimed",
        "key_id": verified["key_id"],
        "intent_id": verified["intent_id"],
        "prediction_id": verified["prediction_id"],
        "paper_order_id": verified["paper_order_id"],
        "request_sha256": verified["request_sha256"],
        "prepared_sha256": verified["prepared_sha256"],
        "approval_sha256": verified["approval_sha256"],
        "approval_source_sha256": verified["approval_source_sha256"],
        "origin_attestation_sha256": verified["origin_attestation_sha256"],
        "origin_key_id": verified["origin_key_id"],
        "transport_nonce": verified["transport_nonce"],
        "claimed_at": _utc(observed_at).isoformat(),
        "retry_allowed": False,
    }
    if "source_truth_authorization_sha256" in verified:
        record["source_truth_authorization_sha256"] = verified[
            "source_truth_authorization_sha256"
        ]
    encoded = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
    try:
        fd = os.open(
            claim_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError as exc:
        raise TransportError("transport envelope already claimed") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())

    return {
        **verified,
        "claim_id": claim_key,
        "claim_path": str(claim_path),
        "claim_sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    }
