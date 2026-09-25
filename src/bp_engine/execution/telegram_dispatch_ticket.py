from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bp_engine.execution.telegram_pre_execution import (
    PRE_EXECUTION_PURPOSE,
    PRE_EXECUTION_SCHEMA_VERSION,
    PreExecutionError,
    verify_pre_execution_snapshot,
)

DISPATCH_TICKET_SCHEMA_VERSION = 1
DISPATCH_TICKET_PURPOSE = "phase15-v3-telegram-dispatch-ticket-v1"

BOUND_FIELDS = (
    "transport_key_id",
    "origin_key_id",
    "intent_id",
    "prediction_id",
    "paper_order_id",
    "request_sha256",
    "prepared_sha256",
    "approval_sha256",
    "approval_source_sha256",
    "origin_attestation_sha256",
    "origin_attested_at",
    "origin_expires_at",
    "source_truth_sha256",
    "authorization_report_sha256",
)

SIGNED_SOURCE_TRUTH_BOUND_FIELDS = (
    "source_truth_authorization_sha256",
    "authorization_snapshot_sha256",
    "source_truth_attested_at",
    "source_truth_expires_at",
)


class DispatchTicketError(RuntimeError):
    pass


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
        raise DispatchTicketError("dispatch ticket payload is not canonicalizable") from exc


def payload_sha256(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def pre_execution_report_sha256(report: Mapping[str, Any]) -> str:
    body = dict(report)
    supplied = body.pop("authorization_report_sha256", None)
    if not isinstance(supplied, str) or re.fullmatch(r"[0-9a-f]{64}", supplied) is None:
        raise DispatchTicketError("pre-execution authorization report hash invalid")
    calculated = hashlib.sha256(_canonical(body)).hexdigest()
    if supplied != calculated:
        raise DispatchTicketError("pre-execution authorization report hash mismatch")
    return supplied


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DispatchTicketError("dispatch ticket datetime must be timezone-aware")
    return value.astimezone(UTC)


def _parse_timestamp(report: Mapping[str, Any], name: str) -> datetime:
    try:
        value = datetime.fromisoformat(str(report[name]))
    except (KeyError, TypeError, ValueError) as exc:
        raise DispatchTicketError(f"pre-execution {name} invalid") from exc
    return _utc(value)


def create_dispatch_ticket(
    pre_execution_report: Mapping[str, Any],
    *,
    created_at: datetime,
) -> dict[str, Any]:
    created = _utc(created_at)

    if pre_execution_report.get("schema_version") != PRE_EXECUTION_SCHEMA_VERSION:
        raise DispatchTicketError("pre-execution schema mismatch")
    if pre_execution_report.get("purpose") != PRE_EXECUTION_PURPOSE:
        raise DispatchTicketError("pre-execution purpose mismatch")
    if pre_execution_report.get("status") != "pre_execution_authorized":
        raise DispatchTicketError("pre-execution report is not authorized")
    if pre_execution_report.get("authorized") is not True:
        raise DispatchTicketError("pre-execution authorization flag invalid")
    if pre_execution_report.get("blockers") != []:
        raise DispatchTicketError("pre-execution report contains blockers")
    for name in (
        "retry_allowed",
        "mutation_performed",
        "network_action_performed",
        "executor_invoked",
        "real_order_submitted",
    ):
        if pre_execution_report.get(name) is not False:
            raise DispatchTicketError(f"pre-execution {name} must be false")

    report_hash = pre_execution_report_sha256(pre_execution_report)

    bound: dict[str, str] = {}
    for name in BOUND_FIELDS:
        value = str(pre_execution_report.get(name) or "")
        if not value:
            raise DispatchTicketError(f"pre-execution {name} missing")
        bound[name] = value

    for name in (
        "request_sha256",
        "prepared_sha256",
        "approval_sha256",
        "approval_source_sha256",
        "origin_attestation_sha256",
        "source_truth_sha256",
        "authorization_report_sha256",
    ):
        if re.fullmatch(r"[0-9a-f]{64}", bound[name]) is None:
            raise DispatchTicketError(f"pre-execution {name} invalid")

    if bound["authorization_report_sha256"] != report_hash:
        raise DispatchTicketError("pre-execution authorization report hash changed")

    origin_attested_at = _parse_timestamp(pre_execution_report, "origin_attested_at")
    origin_expires_at = _parse_timestamp(pre_execution_report, "origin_expires_at")
    if origin_expires_at <= origin_attested_at:
        raise DispatchTicketError("origin attestation expiry invalid")
    if created < origin_attested_at:
        raise DispatchTicketError("dispatch ticket predates origin attestation")
    if created >= origin_expires_at:
        raise DispatchTicketError("dispatch ticket origin authorization expired")

    source_truth_bound: dict[str, str] = {}
    supplied_source_truth_fields = [
        name in pre_execution_report
        for name in SIGNED_SOURCE_TRUTH_BOUND_FIELDS
    ]
    expires_at = origin_expires_at
    if any(supplied_source_truth_fields):
        if not all(supplied_source_truth_fields):
            raise DispatchTicketError(
                "pre-execution signed source truth binding incomplete"
            )
        for name in (
            "source_truth_authorization_sha256",
            "authorization_snapshot_sha256",
        ):
            value = str(pre_execution_report.get(name) or "")
            if re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise DispatchTicketError(f"pre-execution {name} invalid")
            source_truth_bound[name] = value

        source_truth_attested_at = _parse_timestamp(
            pre_execution_report,
            "source_truth_attested_at",
        )
        source_truth_expires_at = _parse_timestamp(
            pre_execution_report,
            "source_truth_expires_at",
        )
        if source_truth_expires_at <= source_truth_attested_at:
            raise DispatchTicketError(
                "source truth authorization expiry invalid"
            )
        if source_truth_expires_at > origin_expires_at:
            raise DispatchTicketError(
                "source truth authorization outlives origin attestation"
            )
        if created < source_truth_attested_at:
            raise DispatchTicketError(
                "dispatch ticket predates source truth authorization"
            )
        if created >= source_truth_expires_at:
            raise DispatchTicketError(
                "dispatch ticket source truth authorization expired"
            )
        source_truth_bound["source_truth_attested_at"] = (
            source_truth_attested_at.isoformat()
        )
        source_truth_bound["source_truth_expires_at"] = (
            source_truth_expires_at.isoformat()
        )
        expires_at = min(origin_expires_at, source_truth_expires_at)

    body: dict[str, Any] = {
        "schema_version": DISPATCH_TICKET_SCHEMA_VERSION,
        "purpose": DISPATCH_TICKET_PURPOSE,
        "status": "dispatch_ticket_prepared",
        **bound,
        **source_truth_bound,
        "created_at": created.isoformat(),
        "expires_at": expires_at.isoformat(),
        "retry_allowed": False,
        "mutation_performed": False,
        "network_action_performed": False,
        "executor_invoked": False,
        "real_order_submitted": False,
    }
    ticket_hash = payload_sha256(body)
    return {
        **body,
        "dispatch_ticket_sha256": ticket_hash,
    }


def verify_dispatch_ticket(
    ticket: Mapping[str, Any],
    *,
    observed_at: datetime,
) -> dict[str, Any]:
    observed = _utc(observed_at)
    if ticket.get("schema_version") != DISPATCH_TICKET_SCHEMA_VERSION:
        raise DispatchTicketError("dispatch ticket schema mismatch")
    if ticket.get("purpose") != DISPATCH_TICKET_PURPOSE:
        raise DispatchTicketError("dispatch ticket purpose mismatch")
    if ticket.get("status") != "dispatch_ticket_prepared":
        raise DispatchTicketError("dispatch ticket status invalid")
    for name in (
        "retry_allowed",
        "mutation_performed",
        "network_action_performed",
        "executor_invoked",
        "real_order_submitted",
    ):
        if ticket.get(name) is not False:
            raise DispatchTicketError(f"dispatch ticket {name} must be false")

    supplied_hash = str(ticket.get("dispatch_ticket_sha256") or "")
    if re.fullmatch(r"[0-9a-f]{64}", supplied_hash) is None:
        raise DispatchTicketError("dispatch ticket hash invalid")
    body = dict(ticket)
    body.pop("dispatch_ticket_sha256", None)
    if supplied_hash != payload_sha256(body):
        raise DispatchTicketError("dispatch ticket hash mismatch")

    for name in BOUND_FIELDS:
        if not str(ticket.get(name) or ""):
            raise DispatchTicketError(f"dispatch ticket {name} missing")

    signed_source_truth_present = [
        name in ticket
        for name in SIGNED_SOURCE_TRUTH_BOUND_FIELDS
    ]
    if any(signed_source_truth_present):
        if not all(signed_source_truth_present):
            raise DispatchTicketError(
                "dispatch ticket signed source truth binding incomplete"
            )
        for name in (
            "source_truth_authorization_sha256",
            "authorization_snapshot_sha256",
        ):
            if re.fullmatch(
                r"[0-9a-f]{64}",
                str(ticket.get(name) or ""),
            ) is None:
                raise DispatchTicketError(f"dispatch ticket {name} invalid")

    created = _parse_timestamp(ticket, "created_at")
    expires = _parse_timestamp(ticket, "expires_at")
    if expires <= created:
        raise DispatchTicketError("dispatch ticket expiry invalid")
    if observed < created:
        raise DispatchTicketError("dispatch ticket is from the future")
    if observed >= expires:
        raise DispatchTicketError("dispatch ticket expired")
    if all(signed_source_truth_present):
        source_truth_attested = _parse_timestamp(
            ticket,
            "source_truth_attested_at",
        )
        source_truth_expires = _parse_timestamp(
            ticket,
            "source_truth_expires_at",
        )
        if source_truth_expires <= source_truth_attested:
            raise DispatchTicketError(
                "dispatch ticket source truth expiry invalid"
            )
        if created < source_truth_attested:
            raise DispatchTicketError(
                "dispatch ticket predates source truth authorization"
            )
        if expires > source_truth_expires:
            raise DispatchTicketError(
                "dispatch ticket outlives source truth authorization"
            )

    return dict(ticket)


def verify_dispatch_ticket_against_report(
    ticket: Mapping[str, Any],
    *,
    pre_execution_report: Mapping[str, Any],
    observed_at: datetime,
    ready_verification: Mapping[str, Any] | None = None,
    project_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    verified = verify_dispatch_ticket(ticket, observed_at=observed_at)
    report = pre_execution_report
    if ready_verification is None and project_state is not None:
        raise DispatchTicketError(
            "project state requires current ready verification"
        )
    if ready_verification is not None:
        try:
            report = verify_pre_execution_snapshot(
                pre_execution_report,
                ready_verification=ready_verification,
                project_state=project_state,
                require_authorized=True,
            )
        except PreExecutionError as exc:
            raise DispatchTicketError(
                f"pre-execution snapshot invalid: {exc}"
            ) from exc

    created_at = _parse_timestamp(verified, "created_at")
    expected = create_dispatch_ticket(
        report,
        created_at=created_at,
    )
    if dict(verified) != expected:
        raise DispatchTicketError(
            "dispatch ticket does not match pre-execution authorization report"
        )
    return verified


def _ensure_private_directory(path: Path, *, label: str) -> None:
    try:
        path.mkdir(parents=True, mode=0o700)
    except FileExistsError:
        pass
    try:
        info = path.lstat()
    except OSError as exc:
        raise DispatchTicketError(f"{label} is not accessible") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise DispatchTicketError(f"{label} must be a non-symlink directory")
    os.chmod(path, 0o700)


def claim_dispatch_ticket(
    ticket: Mapping[str, Any],
    *,
    pre_execution_report: Mapping[str, Any],
    ready_verification: Mapping[str, Any],
    project_state: Mapping[str, Any] | None,
    observed_at: datetime,
    state_dir: Path,
) -> dict[str, Any]:
    try:
        current_report = verify_pre_execution_snapshot(
            pre_execution_report,
            ready_verification=ready_verification,
            project_state=project_state,
            require_authorized=True,
        )
    except PreExecutionError as exc:
        raise DispatchTicketError(
            f"pre-execution snapshot invalid: {exc}"
        ) from exc

    verified = verify_dispatch_ticket_against_report(
        ticket,
        pre_execution_report=current_report,
        observed_at=observed_at,
    )
    _ensure_private_directory(state_dir, label="dispatch claim directory")

    claim_id = hashlib.sha256(
        (
            f"{verified['intent_id']}\0"
            f"{verified['request_sha256']}"
        ).encode()
    ).hexdigest()
    claim_path = state_dir / f"{claim_id}.json"

    record = {
        "schema_version": 1,
        "status": "dispatch_claimed",
        "dispatch_ticket_sha256": str(verified["dispatch_ticket_sha256"]),
        "authorization_report_sha256": str(
            verified["authorization_report_sha256"]
        ),
        "source_truth_sha256": str(verified["source_truth_sha256"]),
        "transport_key_id": str(verified["transport_key_id"]),
        "origin_key_id": str(verified["origin_key_id"]),
        "intent_id": str(verified["intent_id"]),
        "prediction_id": str(verified["prediction_id"]),
        "paper_order_id": str(verified["paper_order_id"]),
        "request_sha256": str(verified["request_sha256"]),
        "prepared_sha256": str(verified["prepared_sha256"]),
        "approval_sha256": str(verified["approval_sha256"]),
        "approval_source_sha256": str(verified["approval_source_sha256"]),
        "origin_attestation_sha256": str(
            verified["origin_attestation_sha256"]
        ),
        "expires_at": str(verified["expires_at"]),
        "claimed_at": _utc(observed_at).isoformat(),
        "retry_allowed": False,
        "executor_invoked": False,
        "real_order_submitted": False,
    }
    encoded = json.dumps(
        record,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ) + "\n"
    try:
        fd = os.open(
            claim_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError as exc:
        raise DispatchTicketError("dispatch ticket already claimed") from exc

    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())

    return {
        **record,
        "claim_id": claim_id,
        "claim_path": str(claim_path),
        "claim_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
    }

