from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

PRE_EXECUTION_SCHEMA_VERSION = 1
PRE_EXECUTION_PURPOSE = "phase15-v3-telegram-pre-execution-v1"


class PreExecutionError(RuntimeError):
    pass


PROJECT_STATE_AUTHORIZATION_SNAPSHOT_FIELDS = frozenset(
    {
        "source_of_truth_version",
        "global_live_trading_enabled",
        "phase15_live_trading_enabled",
        "phase15_canary_authorized",
        "canary_order_submitted",
        "first_canary_reconciliation_complete",
        "pending_unsubmitted_intent_present",
        "v3_strategy_mutation_performed",
        "second_order_authorized",
        "automated_real_money_submission",
        "manual_real_money_submission_required",
        "telegram_one_tap_submission_authorized",
        "telegram_persistent_execution_transport_authorized",
        "telegram_pubsub_transport_authorized",
    }
)


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


def project_state_authorization_snapshot(
    project_state: Mapping[str, Any],
) -> dict[str, Any]:
    phase = project_state.get("phase_15_v3_live_canary")
    if not isinstance(phase, Mapping):
        raise PreExecutionError("phase 15 source truth missing")

    first_canary = phase.get("first_live_canary")
    if not isinstance(first_canary, Mapping):
        first_canary = {}

    return {
        "source_of_truth_version": str(
            project_state.get("source_of_truth_version") or ""
        ),
        "global_live_trading_enabled": project_state.get(
            "live_trading_enabled"
        ),
        "phase15_live_trading_enabled": phase.get("live_trading_enabled"),
        "phase15_canary_authorized": phase.get("phase15_canary_authorized"),
        "canary_order_submitted": phase.get("canary_order_submitted"),
        "first_canary_reconciliation_complete": first_canary.get(
            "official_reconciliation_complete"
        ),
        "pending_unsubmitted_intent_present": (
            phase.get("pending_unsubmitted_intent") is not None
        ),
        "v3_strategy_mutation_performed": phase.get(
            "v3_strategy_mutation_performed"
        ),
        "second_order_authorized": phase.get("second_order_authorized"),
        "automated_real_money_submission": phase.get(
            "automated_real_money_submission"
        ),
        "manual_real_money_submission_required": phase.get(
            "manual_real_money_submission_required"
        ),
        "telegram_one_tap_submission_authorized": phase.get(
            "telegram_one_tap_submission_authorized"
        ),
        "telegram_persistent_execution_transport_authorized": phase.get(
            "telegram_persistent_execution_transport_authorized"
        ),
        "telegram_pubsub_transport_authorized": phase.get(
            "telegram_pubsub_transport_authorized"
        ),
    }


def authorization_snapshot_blockers(
    snapshot: Mapping[str, Any],
) -> list[str]:
    if set(snapshot) != PROJECT_STATE_AUTHORIZATION_SNAPSHOT_FIELDS:
        raise PreExecutionError("source truth authorization snapshot fields mismatch")

    blockers: list[str] = []
    if snapshot.get("global_live_trading_enabled") is not False:
        blockers.append("global_live_trading_not_safely_disabled")
    if snapshot.get("phase15_live_trading_enabled") is not False:
        blockers.append("phase15_live_trading_not_safely_disabled")
    if snapshot.get("phase15_canary_authorized") is not True:
        blockers.append("phase15_canary_not_authorized")
    if snapshot.get("canary_order_submitted") is not True:
        blockers.append("first_canary_not_submitted")
    if snapshot.get("first_canary_reconciliation_complete") is not True:
        blockers.append("first_canary_reconciliation_not_complete")
    if snapshot.get("pending_unsubmitted_intent_present") is not False:
        blockers.append("pending_unsubmitted_intent_present")
    if snapshot.get("v3_strategy_mutation_performed") is not False:
        blockers.append("v3_strategy_mutation_detected")
    if snapshot.get("second_order_authorized") is not True:
        blockers.append("second_order_not_authorized")
    if snapshot.get("automated_real_money_submission") is not True:
        blockers.append("automated_real_money_submission_not_authorized")
    if snapshot.get("manual_real_money_submission_required") is not False:
        blockers.append("manual_submission_still_required")
    if snapshot.get("telegram_one_tap_submission_authorized") is not True:
        blockers.append("telegram_one_tap_not_authorized")
    if (
        snapshot.get("telegram_persistent_execution_transport_authorized")
        is not True
    ):
        blockers.append("persistent_execution_transport_not_authorized")
    if snapshot.get("telegram_pubsub_transport_authorized") is not True:
        blockers.append("telegram_pubsub_transport_not_authorized")
    return blockers


def project_state_authorization_blockers(
    project_state: Mapping[str, Any],
) -> list[str]:
    return authorization_snapshot_blockers(
        project_state_authorization_snapshot(project_state)
    )


def evaluate_pre_execution_authorization(
    *,
    ready_verification: Mapping[str, Any],
    project_state: Mapping[str, Any],
) -> dict[str, Any]:
    ready_status = str(ready_verification.get("status") or "")
    if ready_status not in {
        "execution_ready_origin_verified",
        "execution_ready_source_truth_verified",
    }:
        raise PreExecutionError("ready bundle has not passed origin verification")
    if ready_verification.get("retry_allowed") is not False:
        raise PreExecutionError("ready bundle retry policy invalid")
    if ready_verification.get("executor_invoked") is not False:
        raise PreExecutionError("ready bundle executor state invalid")
    if ready_verification.get("real_order_submitted") is not False:
        raise PreExecutionError("ready bundle money state invalid")

    blockers = project_state_authorization_blockers(project_state)
    current_source_truth_sha256 = source_truth_sha256(project_state)

    v2_binding: dict[str, Any] = {}
    if ready_status == "execution_ready_source_truth_verified":
        if ready_verification.get("source_truth_authorized") is not True:
            raise PreExecutionError(
                "ready source truth authorization is not authorized"
            )
        if ready_verification.get("source_truth_blockers") != []:
            raise PreExecutionError(
                "ready source truth authorization contains blockers"
            )
        signed_project_state_sha256 = str(
            ready_verification.get("project_state_sha256") or ""
        )
        if re.fullmatch(r"[0-9a-f]{64}", signed_project_state_sha256) is None:
            raise PreExecutionError(
                "ready source truth project state hash invalid"
            )
        if signed_project_state_sha256 != current_source_truth_sha256:
            raise PreExecutionError(
                "ready source truth project state hash mismatch"
            )

        current_snapshot_sha256 = hashlib.sha256(
            _canonical(project_state_authorization_snapshot(project_state))
        ).hexdigest()
        signed_snapshot_sha256 = str(
            ready_verification.get("authorization_snapshot_sha256") or ""
        )
        if signed_snapshot_sha256 != current_snapshot_sha256:
            raise PreExecutionError(
                "ready source truth authorization snapshot hash mismatch"
            )

        source_truth_authorization_sha256 = str(
            ready_verification.get("source_truth_authorization_sha256") or ""
        )
        if (
            re.fullmatch(
                r"[0-9a-f]{64}",
                source_truth_authorization_sha256,
            )
            is None
        ):
            raise PreExecutionError(
                "ready source truth authorization hash invalid"
            )
        source_truth_attested_at = str(
            ready_verification.get("source_truth_attested_at") or ""
        )
        source_truth_expires_at = str(
            ready_verification.get("source_truth_expires_at") or ""
        )
        if not source_truth_attested_at or not source_truth_expires_at:
            raise PreExecutionError(
                "ready source truth authorization timestamps missing"
            )
        if blockers:
            raise PreExecutionError(
                "current source truth no longer authorizes execution"
            )
        v2_binding = {
            "source_truth_authorization_sha256": (
                source_truth_authorization_sha256
            ),
            "authorization_snapshot_sha256": signed_snapshot_sha256,
            "source_truth_attested_at": source_truth_attested_at,
            "source_truth_expires_at": source_truth_expires_at,
        }

    report = {
        "schema_version": PRE_EXECUTION_SCHEMA_VERSION,
        "purpose": PRE_EXECUTION_PURPOSE,
        "status": "pre_execution_authorized" if not blockers else "pre_execution_blocked",
        "authorized": not blockers,
        "blockers": blockers,
        "source_truth_sha256": current_source_truth_sha256,
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
        **v2_binding,
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
