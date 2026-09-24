from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

PRE_EXECUTION_SCHEMA_VERSION = 1
PRE_EXECUTION_PURPOSE = "phase15-v3-telegram-pre-execution-v1"


class PreExecutionError(RuntimeError):
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
        raise PreExecutionError("pre-execution payload is not canonicalizable") from exc


def source_truth_sha256(state: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(state)).hexdigest()


def evaluate_pre_execution_authorization(
    *,
    ready_verification: Mapping[str, Any],
    project_state: Mapping[str, Any],
) -> dict[str, Any]:
    if ready_verification.get("status") != "execution_ready_origin_verified":
        raise PreExecutionError("ready bundle has not passed origin verification")
    if ready_verification.get("retry_allowed") is not False:
        raise PreExecutionError("ready bundle retry policy invalid")
    if ready_verification.get("executor_invoked") is not False:
        raise PreExecutionError("ready bundle executor state invalid")
    if ready_verification.get("real_order_submitted") is not False:
        raise PreExecutionError("ready bundle money state invalid")

    phase = project_state.get("phase_15_v3_live_canary")
    if not isinstance(phase, Mapping):
        raise PreExecutionError("phase 15 source truth missing")

    first_canary = phase.get("first_live_canary")
    if not isinstance(first_canary, Mapping):
        first_canary = {}

    blockers: list[str] = []

    if project_state.get("live_trading_enabled") is not False:
        blockers.append("global_live_trading_not_safely_disabled")
    if phase.get("live_trading_enabled") is not False:
        blockers.append("phase15_live_trading_not_safely_disabled")
    if phase.get("phase15_canary_authorized") is not True:
        blockers.append("phase15_canary_not_authorized")
    if phase.get("canary_order_submitted") is not True:
        blockers.append("first_canary_not_submitted")
    if first_canary.get("official_reconciliation_complete") is not True:
        blockers.append("first_canary_reconciliation_not_complete")
    if phase.get("pending_unsubmitted_intent") is not None:
        blockers.append("pending_unsubmitted_intent_present")
    if phase.get("v3_strategy_mutation_performed") is not False:
        blockers.append("v3_strategy_mutation_detected")

    if phase.get("second_order_authorized") is not True:
        blockers.append("second_order_not_authorized")
    if phase.get("automated_real_money_submission") is not True:
        blockers.append("automated_real_money_submission_not_authorized")
    if phase.get("manual_real_money_submission_required") is not False:
        blockers.append("manual_submission_still_required")
    if phase.get("telegram_one_tap_submission_authorized") is not True:
        blockers.append("telegram_one_tap_not_authorized")
    if phase.get("telegram_persistent_execution_transport_authorized") is not True:
        blockers.append("persistent_execution_transport_not_authorized")
    if phase.get("telegram_pubsub_transport_authorized") is not True:
        blockers.append("telegram_pubsub_transport_not_authorized")

    report = {
        "schema_version": PRE_EXECUTION_SCHEMA_VERSION,
        "purpose": PRE_EXECUTION_PURPOSE,
        "status": "pre_execution_authorized" if not blockers else "pre_execution_blocked",
        "authorized": not blockers,
        "blockers": blockers,
        "source_truth_sha256": source_truth_sha256(project_state),
        "transport_key_id": str(ready_verification.get("transport_key_id") or ""),
        "origin_key_id": str(ready_verification.get("origin_key_id") or ""),
        "intent_id": str(ready_verification.get("intent_id") or ""),
        "prediction_id": str(ready_verification.get("prediction_id") or ""),
        "paper_order_id": str(ready_verification.get("paper_order_id") or ""),
        "request_sha256": str(ready_verification.get("request_sha256") or ""),
        "prepared_sha256": str(ready_verification.get("prepared_sha256") or ""),
        "approval_sha256": str(ready_verification.get("approval_sha256") or ""),
        "approval_source_sha256": str(
            ready_verification.get("approval_source_sha256") or ""
        ),
        "origin_attestation_sha256": str(
            ready_verification.get("origin_attestation_sha256") or ""
        ),
        "origin_attested_at": str(ready_verification.get("origin_attested_at") or ""),
        "origin_expires_at": str(ready_verification.get("origin_expires_at") or ""),
        "retry_allowed": False,
        "mutation_performed": False,
        "network_action_performed": False,
        "executor_invoked": False,
        "real_order_submitted": False,
    }
    for name in (
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
    ):
        if not report[name]:
            raise PreExecutionError(f"ready bundle {name} missing")
    report["authorization_report_sha256"] = hashlib.sha256(_canonical(report)).hexdigest()
    return report


def verify_pre_execution_snapshot(
    snapshot: Mapping[str, Any],
    *,
    ready_verification: Mapping[str, Any],
    project_state: Mapping[str, Any],
    require_authorized: bool = False,
) -> dict[str, Any]:
    fresh = evaluate_pre_execution_authorization(
        ready_verification=ready_verification,
        project_state=project_state,
    )
    if dict(snapshot) != fresh:
        raise PreExecutionError("pre-execution snapshot is stale or modified")
    if require_authorized and fresh["authorized"] is not True:
        raise PreExecutionError("pre-execution snapshot is not authorized")
    return fresh
