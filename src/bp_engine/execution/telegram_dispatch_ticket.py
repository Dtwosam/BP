from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from bp_engine.execution.telegram_pre_execution import (
    PRE_EXECUTION_PURPOSE,
    PRE_EXECUTION_SCHEMA_VERSION,
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

    body: dict[str, Any] = {
        "schema_version": DISPATCH_TICKET_SCHEMA_VERSION,
        "purpose": DISPATCH_TICKET_PURPOSE,
        "status": "dispatch_ticket_prepared",
        **bound,
        "created_at": created.isoformat(),
        "expires_at": origin_expires_at.isoformat(),
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

    created = _parse_timestamp(ticket, "created_at")
    expires = _parse_timestamp(ticket, "expires_at")
    if expires <= created:
        raise DispatchTicketError("dispatch ticket expiry invalid")
    if observed < created:
        raise DispatchTicketError("dispatch ticket is from the future")
    if observed >= expires:
        raise DispatchTicketError("dispatch ticket expired")

    return dict(ticket)
