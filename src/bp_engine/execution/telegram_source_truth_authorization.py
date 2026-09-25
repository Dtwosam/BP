from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from bp_engine.execution.telegram_origin_attestation import (
    OriginAttestationError,
    execution_approval,
    payload_sha256 as origin_payload_sha256,
    verify_origin_attestation,
)
from bp_engine.execution.telegram_pre_execution import (
    PROJECT_STATE_AUTHORIZATION_SNAPSHOT_FIELDS,
    PreExecutionError,
    authorization_snapshot_blockers,
    project_state_authorization_snapshot,
    source_truth_sha256,
)

SOURCE_TRUTH_AUTHORIZATION_SCHEMA_VERSION = 1
SOURCE_TRUTH_AUTHORIZATION_PURPOSE = (
    "phase15-v3-telegram-source-truth-authorization-v1"
)
SOURCE_TRUTH_AUTHORIZATION_MAX_LIFETIME_SECONDS = 15
SOURCE_TRUTH_AUTHORIZATION_FIELDS = frozenset(
    {
        "schema_version",
        "purpose",
        "key_id",
        "project_state_sha256",
        "authorization_snapshot_sha256",
        "authorization_snapshot",
        "authorized",
        "blockers",
        "intent_id",
        "prediction_id",
        "paper_order_id",
        "request_sha256",
        "prepared_sha256",
        "approval_sha256",
        "approval_source_sha256",
        "origin_attestation_sha256",
        "origin_attested_at",
        "attested_at",
        "expires_at",
        "hmac_sha256",
    }
)


class SourceTruthAuthorizationError(RuntimeError):
    pass


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SourceTruthAuthorizationError(
            "source truth authorization datetime must be timezone-aware"
        )
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
        raise SourceTruthAuthorizationError(
            "source truth authorization is not canonicalizable"
        ) from exc


def payload_sha256(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _key_id(value: str) -> str:
    normalized = value.strip()
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
    if (
        not normalized
        or len(normalized.encode("utf-8")) > 64
        or any(ch not in allowed for ch in normalized)
    ):
        raise SourceTruthAuthorizationError(
            "source truth authorization key id invalid"
        )
    return normalized


def _mac(body: Mapping[str, Any], key: bytes) -> str:
    if len(key) != 32:
        raise SourceTruthAuthorizationError(
            "source truth authorization key must be exactly 32 bytes"
        )
    return hmac.new(key, _canonical(body), hashlib.sha256).hexdigest()


def create_source_truth_authorization(
    project_state: Mapping[str, Any],
    *,
    prepared: Mapping[str, Any],
    approval: Mapping[str, Any],
    origin_attestation: Mapping[str, Any],
    key: bytes,
    key_id: str,
    attested_at: datetime,
) -> dict[str, Any]:
    attested = _utc(attested_at)
    normalized_key_id = _key_id(key_id)
    try:
        origin = verify_origin_attestation(
            origin_attestation,
            prepared=prepared,
            approval=approval,
            key=key,
            expected_key_id=normalized_key_id,
            observed_at=attested,
        )
        snapshot = project_state_authorization_snapshot(project_state)
        blockers = authorization_snapshot_blockers(snapshot)
    except (OriginAttestationError, PreExecutionError) as exc:
        raise SourceTruthAuthorizationError(str(exc)) from exc

    approval_source_hash = origin_payload_sha256(approval)
    if origin["approval_source_sha256"] != approval_source_hash:
        raise SourceTruthAuthorizationError(
            "origin attestation approval source hash mismatch"
        )

    origin_expires = _utc(datetime.fromisoformat(str(origin["expires_at"])))
    expires = min(
        attested
        + timedelta(seconds=SOURCE_TRUTH_AUTHORIZATION_MAX_LIFETIME_SECONDS),
        origin_expires,
    )
    if expires <= attested:
        raise SourceTruthAuthorizationError(
            "source truth authorization window already closed"
        )

    body: dict[str, Any] = {
        "schema_version": SOURCE_TRUTH_AUTHORIZATION_SCHEMA_VERSION,
        "purpose": SOURCE_TRUTH_AUTHORIZATION_PURPOSE,
        "key_id": normalized_key_id,
        "project_state_sha256": source_truth_sha256(project_state),
        "authorization_snapshot_sha256": payload_sha256(snapshot),
        "authorization_snapshot": snapshot,
        "authorized": not blockers,
        "blockers": blockers,
        "intent_id": str(origin["intent_id"]),
        "prediction_id": str(origin["prediction_id"]),
        "paper_order_id": str(origin["paper_order_id"]),
        "request_sha256": str(origin["request_sha256"]),
        "prepared_sha256": str(origin["prepared_sha256"]),
        "approval_sha256": str(origin["approval_sha256"]),
        "approval_source_sha256": approval_source_hash,
        "origin_attestation_sha256": origin_payload_sha256(origin_attestation),
        "origin_attested_at": str(origin["attested_at"]),
        "attested_at": attested.isoformat(),
        "expires_at": expires.isoformat(),
    }
    return {
        **body,
        "hmac_sha256": _mac(body, key),
    }


def verify_source_truth_authorization(
    attestation: Mapping[str, Any],
    *,
    prepared: Mapping[str, Any],
    approval: Mapping[str, Any],
    origin_attestation: Mapping[str, Any],
    key: bytes,
    expected_key_id: str,
    observed_at: datetime,
    require_authorized: bool = False,
) -> dict[str, Any]:
    observed = _utc(observed_at)
    normalized_key_id = _key_id(expected_key_id)
    if set(attestation) != SOURCE_TRUTH_AUTHORIZATION_FIELDS:
        raise SourceTruthAuthorizationError(
            "source truth authorization fields mismatch"
        )
    if (
        attestation.get("schema_version")
        != SOURCE_TRUTH_AUTHORIZATION_SCHEMA_VERSION
    ):
        raise SourceTruthAuthorizationError(
            "source truth authorization schema mismatch"
        )
    if attestation.get("purpose") != SOURCE_TRUTH_AUTHORIZATION_PURPOSE:
        raise SourceTruthAuthorizationError(
            "source truth authorization purpose mismatch"
        )
    if str(attestation.get("key_id") or "") != normalized_key_id:
        raise SourceTruthAuthorizationError(
            "source truth authorization key id mismatch"
        )

    supplied_mac = str(attestation.get("hmac_sha256") or "")
    if re.fullmatch(r"[0-9a-f]{64}", supplied_mac) is None:
        raise SourceTruthAuthorizationError(
            "source truth authorization hmac invalid"
        )
    body = {
        name: value
        for name, value in attestation.items()
        if name != "hmac_sha256"
    }
    if not hmac.compare_digest(supplied_mac, _mac(body, key)):
        raise SourceTruthAuthorizationError(
            "source truth authorization hmac mismatch"
        )

    try:
        attested_at = _utc(datetime.fromisoformat(str(attestation["attested_at"])))
        expires_at = _utc(datetime.fromisoformat(str(attestation["expires_at"])))
    except (KeyError, TypeError, ValueError) as exc:
        raise SourceTruthAuthorizationError(
            "source truth authorization timestamps invalid"
        ) from exc
    if attested_at > observed:
        raise SourceTruthAuthorizationError(
            "source truth authorization is from the future"
        )
    if expires_at <= attested_at:
        raise SourceTruthAuthorizationError(
            "source truth authorization expiry invalid"
        )
    if (
        expires_at - attested_at
    ).total_seconds() > SOURCE_TRUTH_AUTHORIZATION_MAX_LIFETIME_SECONDS:
        raise SourceTruthAuthorizationError(
            "source truth authorization lifetime exceeds maximum"
        )
    if observed >= expires_at:
        raise SourceTruthAuthorizationError(
            "source truth authorization expired"
        )

    try:
        origin = verify_origin_attestation(
            origin_attestation,
            prepared=prepared,
            approval=approval,
            key=key,
            expected_key_id=normalized_key_id,
            observed_at=observed,
        )
    except OriginAttestationError as exc:
        raise SourceTruthAuthorizationError(str(exc)) from exc

    origin_expires = _utc(datetime.fromisoformat(str(origin["expires_at"])))
    if attested_at < _utc(datetime.fromisoformat(str(origin["attested_at"]))):
        raise SourceTruthAuthorizationError(
            "source truth authorization predates origin attestation"
        )
    if expires_at > origin_expires:
        raise SourceTruthAuthorizationError(
            "source truth authorization outlives origin attestation"
        )

    snapshot = attestation.get("authorization_snapshot")
    if not isinstance(snapshot, Mapping):
        raise SourceTruthAuthorizationError(
            "source truth authorization snapshot missing"
        )
    if set(snapshot) != PROJECT_STATE_AUTHORIZATION_SNAPSHOT_FIELDS:
        raise SourceTruthAuthorizationError(
            "source truth authorization snapshot fields mismatch"
        )
    if (
        str(attestation.get("authorization_snapshot_sha256") or "")
        != payload_sha256(snapshot)
    ):
        raise SourceTruthAuthorizationError(
            "source truth authorization snapshot hash mismatch"
        )
    try:
        blockers = authorization_snapshot_blockers(snapshot)
    except PreExecutionError as exc:
        raise SourceTruthAuthorizationError(str(exc)) from exc
    supplied_blockers = attestation.get("blockers")
    if not isinstance(supplied_blockers, list) or supplied_blockers != blockers:
        raise SourceTruthAuthorizationError(
            "source truth authorization blockers mismatch"
        )
    authorized = not blockers
    if attestation.get("authorized") is not authorized:
        raise SourceTruthAuthorizationError(
            "source truth authorization flag mismatch"
        )
    if require_authorized and not authorized:
        raise SourceTruthAuthorizationError(
            "source truth snapshot is not authorized"
        )

    expected = {
        "intent_id": str(origin["intent_id"]),
        "prediction_id": str(origin["prediction_id"]),
        "paper_order_id": str(origin["paper_order_id"]),
        "request_sha256": str(origin["request_sha256"]),
        "prepared_sha256": str(origin["prepared_sha256"]),
        "approval_sha256": str(origin["approval_sha256"]),
        "approval_source_sha256": origin_payload_sha256(approval),
        "origin_attestation_sha256": origin_payload_sha256(origin_attestation),
        "origin_attested_at": str(origin["attested_at"]),
    }
    for name, value in expected.items():
        if str(attestation.get(name) or "") != value:
            raise SourceTruthAuthorizationError(
                f"source truth authorization {name} mismatch"
            )

    project_state_hash = str(attestation.get("project_state_sha256") or "")
    if re.fullmatch(r"[0-9a-f]{64}", project_state_hash) is None:
        raise SourceTruthAuthorizationError(
            "source truth authorization project state hash invalid"
        )
    snapshot_hash = str(attestation.get("authorization_snapshot_sha256") or "")
    if re.fullmatch(r"[0-9a-f]{64}", snapshot_hash) is None:
        raise SourceTruthAuthorizationError(
            "source truth authorization snapshot hash invalid"
        )

    sanitized_approval = execution_approval(approval)
    if expected["approval_sha256"] != origin_payload_sha256(sanitized_approval):
        raise SourceTruthAuthorizationError(
            "source truth authorization approval hash mismatch"
        )

    return {
        "key_id": normalized_key_id,
        "project_state_sha256": project_state_hash,
        "authorization_snapshot_sha256": snapshot_hash,
        "authorization_snapshot": dict(snapshot),
        "authorized": authorized,
        "blockers": list(blockers),
        **expected,
        "attested_at": attested_at.isoformat(),
        "expires_at": expires_at.isoformat(),
    }
