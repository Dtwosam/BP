from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bp_engine.execution.fast_live import (
    FAST_LIVE_AUTO_APPROVAL_CONTRACT_BLOB_SHA,
    FAST_LIVE_CONTINUOUS_AUTO_AUTHORIZATION_MODE_V2,
    FAST_LIVE_EXECUTION_VERSION,
    FAST_LIVE_MAX_TRANSIT_SECONDS,
    FAST_LIVE_PREDICTION_VERSION,
    project_state_sha256,
    verify_source_authorization,
)

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_ACCEPT = "I_ACCEPT_GENERATE_REVIEWABLE_CONTINUOUS_AUTO_LIVE_AUTHORIZATION_CANDIDATE"
_UPGRADE_PURPOSE = "phase15-v3-telegram-auto-approver-continuous-contract-upgrade-v1"
_CANDIDATE_PURPOSE = "phase15-v3-fast-live-continuous-auto-authorization-candidate-v2"
_COMPLETED_CLEANUP_PURPOSE = "phase15-v3-fast-live-completed-session-cleanup-v1"
_ZERO_ATTEMPT_CLEANUP_PURPOSE = (
    "phase15-v3-fast-live-expired-zero-attempt-cleanup-v1"
)


class CandidateError(RuntimeError):
    pass


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CandidateError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _load_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CandidateError(f"{label} JSON invalid") from exc
    if not isinstance(payload, dict):
        raise CandidateError(f"{label} must contain an object")
    return payload


def _raw_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_new_json(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        raise CandidateError(f"output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(
            payload,
            sort_keys=True,
            indent=2,
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, encoded)
        os.fsync(fd)
    finally:
        os.close(fd)


def build_candidate(
    *,
    state: dict[str, Any],
    upgrade_evidence: dict[str, Any],
    expected_main: str,
    authorization_id: str,
    source_of_truth_version: str,
    expires_at: datetime,
    authorized_at: datetime,
    upgrade_evidence_reference: str,
    renew_existing_unactivated: bool = False,
    replace_completed_cleaned_session: bool = False,
    replace_expired_cleaned_zero_attempt_session: bool = False,
    completed_session_cleanup_evidence: dict[str, Any] | None = None,
    completed_session_cleanup_evidence_reference: str = "",
    zero_attempt_cleanup_evidence: dict[str, Any] | None = None,
    zero_attempt_cleanup_evidence_reference: str = "",
) -> dict[str, Any]:
    authorized = _utc(authorized_at)
    expires = _utc(expires_at)
    if not _COMMIT_RE.fullmatch(expected_main):
        raise CandidateError("expected main must be a 40-character lowercase SHA")
    if not authorization_id or len(authorization_id) > 128:
        raise CandidateError("authorization id invalid")
    if not _VERSION_RE.fullmatch(source_of_truth_version):
        raise CandidateError("source-of-truth version invalid")
    if source_of_truth_version == str(state.get("source_of_truth_version") or ""):
        raise CandidateError("source-of-truth version must change")
    if expires <= authorized:
        raise CandidateError("authorization expiry must be in the future")
    if not upgrade_evidence_reference.strip():
        raise CandidateError("upgrade evidence reference missing")
    replacement_mode_count = sum(
        (
            renew_existing_unactivated,
            replace_completed_cleaned_session,
            replace_expired_cleaned_zero_attempt_session,
        )
    )
    if replacement_mode_count > 1:
        raise CandidateError("authorization replacement modes are mutually exclusive")

    if upgrade_evidence.get("schema_version") != 1:
        raise CandidateError("upgrade evidence schema invalid")
    if upgrade_evidence.get("purpose") != _UPGRADE_PURPOSE:
        raise CandidateError("upgrade evidence purpose invalid")
    if upgrade_evidence.get("status") != "UPGRADED_VERIFIED":
        raise CandidateError("upgrade evidence status invalid")
    evidence_main = str(upgrade_evidence.get("repository_main") or "")
    if (
        not renew_existing_unactivated
        and not replace_completed_cleaned_session
        and not replace_expired_cleaned_zero_attempt_session
        and evidence_main != expected_main
    ):
        raise CandidateError("upgrade evidence main mismatch")
    if upgrade_evidence.get("approval_contract_git_blob_sha") != (
        FAST_LIVE_AUTO_APPROVAL_CONTRACT_BLOB_SHA
    ):
        raise CandidateError("upgrade evidence approval contract mismatch")

    required_true = (
        "service_active_after",
        "live_auto_approve_runtime_effective_after",
        "process_live_auto_approve_environment_verified",
        "exact_candidate_prompt_auto_click_prepared_verified",
        "mutated_candidate_prompt_rejected_verified",
    )
    for name in required_true:
        if upgrade_evidence.get(name) is not True:
            raise CandidateError(f"upgrade evidence unsafe: {name}")

    launcher_mode = str(upgrade_evidence.get("launcher_mode") or "")
    if launcher_mode not in {"wrapper", "direct-python"}:
        raise CandidateError("upgrade evidence launcher mode invalid")

    required_false = (
        "telegram_session_deleted",
        "telegram_credentials_replaced",
        "project_state_mutated",
        "live_trading_enabled",
        "real_order_submitted_by_upgrade",
    )
    for name in required_false:
        if upgrade_evidence.get(name) is not False:
            raise CandidateError(f"upgrade evidence unsafe: {name}")

    upgraded_at = _utc(
        datetime.fromisoformat(str(upgrade_evidence.get("observed_at") or ""))
    )

    phase = state.get("phase_15_v3_live_canary")
    if not isinstance(phase, dict):
        raise CandidateError("phase 15 source truth missing")
    auto = phase.get("operator_telegram_auto_approver")
    if not isinstance(auto, dict):
        raise CandidateError("operator auto-approver source truth missing")
    if (
        not str(auto.get("status") or "").startswith("ACTIVE_")
        or auto.get("live_auto_approve_authorized") is not True
    ):
        raise CandidateError("auto-approver must remain active and authorized")
    existing_authorization = phase.get("fast_live_preauthorization")
    replacing_existing = (
        renew_existing_unactivated
        or replace_completed_cleaned_session
        or replace_expired_cleaned_zero_attempt_session
    )
    if replacing_existing:
        if not isinstance(existing_authorization, dict):
            raise CandidateError("replacement requires existing fast live authorization")
        try:
            existing_expires = _utc(
                datetime.fromisoformat(
                    str(existing_authorization.get("expires_at") or "")
                )
            )
        except (TypeError, ValueError) as exc:
            raise CandidateError(
                "existing authorization expiry invalid"
            ) from exc
        existing_validation_observed_at = (
            existing_expires - timedelta(microseconds=1)
            if authorized >= existing_expires
            else authorized
        )
        verify_source_authorization(
            state,
            expected_main=expected_main,
            observed_at=existing_validation_observed_at,
            requires_telegram_approval=True,
            continuous_session=True,
            require_exact_main=False,
        )
        if existing_authorization.get("authorization_mode") != (
            FAST_LIVE_CONTINUOUS_AUTO_AUTHORIZATION_MODE_V2
        ):
            raise CandidateError("existing authorization mode is not replaceable")
        if existing_authorization.get("authorization_id") == authorization_id:
            raise CandidateError("replacement authorization id must change")
        if existing_authorization.get("auto_approver_upgrade_evidence") != (
            upgrade_evidence_reference
        ):
            if renew_existing_unactivated:
                raise CandidateError(
                    "renewal upgrade evidence reference mismatch"
                )
            if replace_expired_cleaned_zero_attempt_session:
                raise CandidateError(
                    "zero-attempt replacement upgrade evidence reference mismatch"
                )
            raise CandidateError(
                "completed-session replacement upgrade evidence reference mismatch"
            )
        if auto.get("continuous_contract_upgrade_evidence") != (
            upgrade_evidence_reference
        ):
            raise CandidateError("auto-approver upgrade evidence reference mismatch")
        if auto.get("continuous_contract_upgrade_main") != evidence_main:
            raise CandidateError("auto-approver upgrade evidence main mismatch")
        if auto.get("approval_contract_git_blob_sha") != (
            FAST_LIVE_AUTO_APPROVAL_CONTRACT_BLOB_SHA
        ):
            raise CandidateError("auto-approver approval contract mismatch")
        if auto.get("continuous_candidate_prompt_authorized") is not True:
            raise CandidateError("continuous candidate prompt not authorized")
        if auto.get("continuous_fast_live_auto_approval_authorized") is not True:
            raise CandidateError("continuous auto approval not authorized")

        if renew_existing_unactivated:
            for field in (
                "deployment_performed",
                "activation_performed",
                "runtime_authorization_created",
                "kill_switch_removed",
                "real_order_submitted",
            ):
                if existing_authorization.get(field) is not False:
                    raise CandidateError(
                        f"existing authorization is not renewable: {field}"
                    )
        elif replace_completed_cleaned_session:
            if authorized < existing_expires:
                raise CandidateError(
                    "completed-session replacement requires expired authorization"
                )
            cleanup = completed_session_cleanup_evidence
            if not isinstance(cleanup, dict):
                raise CandidateError("completed-session cleanup evidence missing")
            if not completed_session_cleanup_evidence_reference.strip():
                raise CandidateError(
                    "completed-session cleanup evidence reference missing"
                )
            if cleanup.get("schema_version") != 1:
                raise CandidateError("completed-session cleanup evidence schema invalid")
            if cleanup.get("purpose") != _COMPLETED_CLEANUP_PURPOSE:
                raise CandidateError("completed-session cleanup evidence purpose invalid")
            if cleanup.get("status") != "CLEANUP_VERIFIED":
                raise CandidateError("completed-session cleanup evidence status invalid")
            exact_matches = {
                "authorization_id": existing_authorization.get("authorization_id"),
                "authorization_mode": existing_authorization.get("authorization_mode"),
                "session_release_main": existing_authorization.get("authorized_at_main"),
                "runtime_expires_at": existing_authorization.get("expires_at"),
                "staged_release_main": expected_main,
                "cleanup_mode": "expired",
                "executor_geo_country": "ZA",
            }
            for name, expected in exact_matches.items():
                if cleanup.get(name) != expected:
                    raise CandidateError(
                        f"completed-session cleanup evidence mismatch: {name}"
                    )
            required_cleanup_true = (
                "cleanup_completed",
                "prior_real_order_submitted",
                "kill_switch_engaged",
                "historical_state_preserved",
                "executor_account_clean",
            )
            for name in required_cleanup_true:
                if cleanup.get(name) is not True:
                    raise CandidateError(
                        f"completed-session cleanup evidence unsafe: {name}"
                    )
            required_cleanup_false = (
                "session_runtime_files_present",
                "session_pubsub_resources_present",
                "recorder_source_active",
                "executor_receiver_active",
                "recorder_runtime_authorization_present",
                "executor_runtime_authorization_present",
                "recorder_transport_key_present",
                "executor_transport_key_present",
                "executor_geo_blocked",
            )
            for name in required_cleanup_false:
                if cleanup.get(name) is not False:
                    raise CandidateError(
                        f"completed-session cleanup evidence unsafe: {name}"
                    )
            if int(cleanup.get("executor_open_order_count", -1)) != 0:
                raise CandidateError(
                    "completed-session cleanup evidence unsafe: executor_open_order_count"
                )
        elif replace_expired_cleaned_zero_attempt_session:
            if authorized < existing_expires:
                raise CandidateError(
                    "zero-attempt replacement requires expired authorization"
                )
            cleanup = zero_attempt_cleanup_evidence
            if not isinstance(cleanup, dict):
                raise CandidateError("zero-attempt cleanup evidence missing")
            if not zero_attempt_cleanup_evidence_reference.strip():
                raise CandidateError(
                    "zero-attempt cleanup evidence reference missing"
                )
            if cleanup.get("schema_version") != 1:
                raise CandidateError("zero-attempt cleanup evidence schema invalid")
            if cleanup.get("purpose") != _ZERO_ATTEMPT_CLEANUP_PURPOSE:
                raise CandidateError("zero-attempt cleanup evidence purpose invalid")
            if cleanup.get("status") != "CLEANUP_VERIFIED":
                raise CandidateError("zero-attempt cleanup evidence status invalid")
            exact_matches = {
                "authorization_id": existing_authorization.get("authorization_id"),
                "authorization_mode": existing_authorization.get("authorization_mode"),
                "session_release_main": existing_authorization.get("authorized_at_main"),
                "runtime_expires_at": existing_authorization.get("expires_at"),
                "cleanup_mode": "expired_zero_attempt",
                "executor_geo_country": "ZA",
            }
            for name, expected in exact_matches.items():
                if cleanup.get(name) != expected:
                    raise CandidateError(
                        f"zero-attempt cleanup evidence mismatch: {name}"
                    )
            required_cleanup_true = (
                "cleanup_completed",
                "zero_network_attempt_verified",
                "kill_switch_engaged",
                "historical_state_preserved",
                "executor_account_clean",
            )
            for name in required_cleanup_true:
                if cleanup.get(name) is not True:
                    raise CandidateError(
                        f"zero-attempt cleanup evidence unsafe: {name}"
                    )
            required_cleanup_false = (
                "prior_real_order_submitted",
                "session_real_order_submitted",
                "session_runtime_files_present",
                "session_pubsub_resources_present",
                "recorder_source_active",
                "executor_receiver_active",
                "recorder_runtime_authorization_present",
                "executor_runtime_authorization_present",
                "recorder_transport_key_present",
                "executor_transport_key_present",
                "executor_geo_blocked",
            )
            for name in required_cleanup_false:
                if cleanup.get(name) is not False:
                    raise CandidateError(
                        f"zero-attempt cleanup evidence unsafe: {name}"
                    )
            if int(cleanup.get("session_network_submission_attempt_count", -1)) != 0:
                raise CandidateError(
                    "zero-attempt cleanup evidence unsafe: "
                    "session_network_submission_attempt_count"
                )
            if int(cleanup.get("session_execution_result_count", -1)) != 0:
                raise CandidateError(
                    "zero-attempt cleanup evidence unsafe: session_execution_result_count"
                )
            publication_count = int(cleanup.get("session_publication_count", -1))
            if publication_count < 0:
                raise CandidateError(
                    "zero-attempt cleanup evidence unsafe: session_publication_count"
                )
            if int(cleanup.get("executor_open_order_count", -1)) != 0:
                raise CandidateError(
                    "zero-attempt cleanup evidence unsafe: executor_open_order_count"
                )
    elif existing_authorization is not None:
        raise CandidateError("fast live preauthorization already exists")
    if state.get("live_trading_enabled") is not False:
        raise CandidateError("global live trading flag must remain false")
    if phase.get("live_trading_enabled") is not False:
        raise CandidateError("phase live trading flag must remain false")

    candidate = copy.deepcopy(state)
    candidate["source_of_truth_version"] = source_of_truth_version
    candidate["updated_at"] = authorized.date().isoformat()
    candidate["current_phase_name"] = (
        "controlled live launch — continuous Telegram auto-approved fast-live candidate"
    )
    candidate["status"] = (
        "PHASE_15_CONTINUOUS_FAST_LIVE_AUTO_APPROVAL_AUTHORIZED_NOT_ACTIVATED"
    )

    candidate_phase = candidate["phase_15_v3_live_canary"]
    candidate_phase["status"] = (
        "CONTINUOUS_FAST_LIVE_AUTO_APPROVAL_AUTHORIZED_NOT_ACTIVATED"
    )

    candidate_auto = candidate_phase["operator_telegram_auto_approver"]
    candidate_auto["approval_contract_git_blob_sha"] = (
        FAST_LIVE_AUTO_APPROVAL_CONTRACT_BLOB_SHA
    )
    candidate_auto["continuous_candidate_prompt_authorized"] = True
    if not (
        renew_existing_unactivated
        or replace_completed_cleaned_session
        or replace_expired_cleaned_zero_attempt_session
    ):
        candidate_auto["continuous_contract_upgrade_main"] = expected_main
        candidate_auto["continuous_contract_upgraded_at"] = upgraded_at.isoformat()
        candidate_auto["continuous_contract_upgrade_evidence"] = (
            upgrade_evidence_reference
        )
    candidate_auto["service_state"] = "running"
    candidate_auto["live_auto_approve_authorized"] = True
    candidate_auto["continuous_fast_live_auto_approval_authorized"] = True

    candidate_phase["fast_live_preauthorization"] = {
        "status": "AUTHORIZED_CONTINUOUS_SESSION",
        "authorized": True,
        "authorization_id": authorization_id,
        "authorization_mode": FAST_LIVE_CONTINUOUS_AUTO_AUTHORIZATION_MODE_V2,
        "authorized_at": authorized.isoformat(),
        "authorized_at_main": expected_main,
        "expires_at": expires.isoformat(),
        "target_notional_usd": 5,
        "max_trade_size_usd": 10,
        "max_total_exposure_usd": 10,
        "max_daily_loss_usd": 10,
        "max_consecutive_losses": 0,
        "min_edge": 0.075,
        "max_transit_seconds": int(FAST_LIVE_MAX_TRANSIT_SECONDS),
        "requires_telegram_approval": True,
        "max_network_submission_attempts_per_intent": 1,
        "prediction_version": FAST_LIVE_PREDICTION_VERSION,
        "execution_version": FAST_LIVE_EXECUTION_VERSION,
        "executor_country": "ZA",
        "auto_approval_contract_git_blob_sha": (
            FAST_LIVE_AUTO_APPROVAL_CONTRACT_BLOB_SHA
        ),
        "auto_approver_upgrade_evidence": upgrade_evidence_reference,
        "continuous_candidate_generated": True,
        "deployment_performed": False,
        "activation_performed": False,
        "kill_switch_removed": False,
        "runtime_authorization_created": False,
        "real_order_submitted": False,
        "stake_growth_authorized": False,
        "v3_strategy_mutation_authorized": False,
        "v4_mutation_authorized": False,
        "broad_autonomous_live_rollout_authorized": False,
    }

    verify_source_authorization(
        candidate,
        expected_main=expected_main,
        observed_at=authorized,
        requires_telegram_approval=True,
        continuous_session=True,
    )
    if candidate["live_trading_enabled"] is not False:
        raise CandidateError("candidate changed global live flag")
    if candidate_phase["live_trading_enabled"] is not False:
        raise CandidateError("candidate changed phase live flag")
    if candidate_auto["live_auto_approve_authorized"] is not True:
        raise CandidateError("candidate disabled auto approval")
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-state", type=Path, required=True)
    parser.add_argument("--auto-approver-upgrade-evidence", type=Path, required=True)
    parser.add_argument("--expected-main", required=True)
    parser.add_argument("--authorization-id", required=True)
    parser.add_argument("--source-of-truth-version", required=True)
    parser.add_argument("--expires-at", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence-output", type=Path, required=True)
    parser.add_argument("--accept-candidate", required=True)
    parser.add_argument(
        "--renew-existing-unactivated",
        action="store_true",
        help=(
            "Replace only an existing unactivated continuous auto authorization, "
            "including an expired one, after re-validating its exact safety contract."
        ),
    )
    parser.add_argument(
        "--replace-completed-cleaned-session",
        action="store_true",
        help=(
            "Replace an expired continuous auto authorization only after exact "
            "completed-session cleanup evidence proves the used session is quiescent."
        ),
    )
    parser.add_argument(
        "--completed-session-cleanup-evidence",
        type=Path,
        help="Reviewed evidence for --replace-completed-cleaned-session.",
    )
    parser.add_argument(
        "--replace-expired-cleaned-zero-attempt-session",
        action="store_true",
        help=(
            "Replace an activated, expired continuous authorization only after "
            "exact cleanup evidence proves the session consumed zero network attempts."
        ),
    )
    parser.add_argument(
        "--zero-attempt-cleanup-evidence",
        type=Path,
        help=(
            "Reviewed cleanup evidence for "
            "--replace-expired-cleaned-zero-attempt-session."
        ),
    )
    args = parser.parse_args()

    if args.accept_candidate != _ACCEPT:
        raise SystemExit("explicit auto candidate generation acceptance required")

    project_state = args.project_state.resolve()
    upgrade_path = args.auto_approver_upgrade_evidence.resolve()
    output = args.output.resolve()
    evidence_output = args.evidence_output.resolve()
    if output == project_state:
        raise SystemExit("candidate generator refuses to overwrite PROJECT_STATE")
    if output == evidence_output:
        raise SystemExit("candidate state and evidence outputs must differ")

    state = _load_object(project_state, label="project state")
    upgrade = _load_object(upgrade_path, label="auto-approver upgrade evidence")
    replacement_mode_count = sum(
        (
            args.renew_existing_unactivated,
            args.replace_completed_cleaned_session,
            args.replace_expired_cleaned_zero_attempt_session,
        )
    )
    if replacement_mode_count > 1:
        raise SystemExit("authorization replacement modes are mutually exclusive")
    cleanup_path = (
        args.completed_session_cleanup_evidence.resolve()
        if args.completed_session_cleanup_evidence is not None
        else None
    )
    cleanup_evidence = (
        _load_object(cleanup_path, label="completed-session cleanup evidence")
        if cleanup_path is not None
        else None
    )
    zero_attempt_cleanup_path = (
        args.zero_attempt_cleanup_evidence.resolve()
        if args.zero_attempt_cleanup_evidence is not None
        else None
    )
    zero_attempt_cleanup_evidence = (
        _load_object(
            zero_attempt_cleanup_path,
            label="zero-attempt cleanup evidence",
        )
        if zero_attempt_cleanup_path is not None
        else None
    )
    if args.replace_completed_cleaned_session and cleanup_path is None:
        raise SystemExit("completed-session cleanup evidence is required")
    if cleanup_path is not None and not args.replace_completed_cleaned_session:
        raise SystemExit(
            "completed-session cleanup evidence requires replacement mode"
        )
    if (
        args.replace_expired_cleaned_zero_attempt_session
        and zero_attempt_cleanup_path is None
    ):
        raise SystemExit("zero-attempt cleanup evidence is required")
    if (
        zero_attempt_cleanup_path is not None
        and not args.replace_expired_cleaned_zero_attempt_session
    ):
        raise SystemExit("zero-attempt cleanup evidence requires replacement mode")
    if (
        not args.renew_existing_unactivated
        and not args.replace_completed_cleaned_session
        and not args.replace_expired_cleaned_zero_attempt_session
        and upgrade.get("project_state_sha256") != _raw_sha256(project_state)
    ):
        raise SystemExit("upgrade evidence project-state hash mismatch")

    try:
        evidence_reference = str(upgrade_path.relative_to(project_state.parent))
    except ValueError as exc:
        raise SystemExit(
            "upgrade evidence must be stored under the repository root"
        ) from exc
    cleanup_evidence_reference = ""
    if cleanup_path is not None:
        try:
            cleanup_evidence_reference = str(
                cleanup_path.relative_to(project_state.parent)
            )
        except ValueError as exc:
            raise SystemExit(
                "completed-session cleanup evidence must be stored under the repository root"
            ) from exc
    zero_attempt_cleanup_evidence_reference = ""
    if zero_attempt_cleanup_path is not None:
        try:
            zero_attempt_cleanup_evidence_reference = str(
                zero_attempt_cleanup_path.relative_to(project_state.parent)
            )
        except ValueError as exc:
            raise SystemExit(
                "zero-attempt cleanup evidence must be stored under the repository root"
            ) from exc

    now = datetime.now(UTC)
    expires = datetime.fromisoformat(args.expires_at)
    candidate = build_candidate(
        state=state,
        upgrade_evidence=upgrade,
        expected_main=args.expected_main,
        authorization_id=args.authorization_id,
        source_of_truth_version=args.source_of_truth_version,
        expires_at=expires,
        authorized_at=now,
        upgrade_evidence_reference=evidence_reference,
        renew_existing_unactivated=args.renew_existing_unactivated,
        replace_completed_cleaned_session=args.replace_completed_cleaned_session,
        replace_expired_cleaned_zero_attempt_session=(
            args.replace_expired_cleaned_zero_attempt_session
        ),
        completed_session_cleanup_evidence=cleanup_evidence,
        completed_session_cleanup_evidence_reference=cleanup_evidence_reference,
        zero_attempt_cleanup_evidence=zero_attempt_cleanup_evidence,
        zero_attempt_cleanup_evidence_reference=(
            zero_attempt_cleanup_evidence_reference
        ),
    )
    candidate_evidence = {
        "schema_version": 1,
        "purpose": _CANDIDATE_PURPOSE,
        "status": "CANDIDATE_GENERATED_NOT_MERGED_NOT_ACTIVATED",
        "source_main": args.expected_main,
        "source_project_state_sha256": _raw_sha256(project_state),
        "auto_approver_upgrade_evidence": evidence_reference,
        "auto_approver_upgrade_evidence_sha256": _raw_sha256(upgrade_path),
        "approval_contract_git_blob_sha": (
            FAST_LIVE_AUTO_APPROVAL_CONTRACT_BLOB_SHA
        ),
        "authorization_id": args.authorization_id,
        "authorization_mode": FAST_LIVE_CONTINUOUS_AUTO_AUTHORIZATION_MODE_V2,
        "source_of_truth_version": args.source_of_truth_version,
        "authorized_at": now.isoformat(),
        "expires_at": _utc(expires).isoformat(),
        "candidate_project_state_sha256": project_state_sha256(candidate),
        "global_live_trading_enabled": False,
        "phase_live_trading_enabled": False,
        "auto_approval_remains_authorized": True,
        "runtime_authorization_created": False,
        "release_staged": bool(args.replace_completed_cleaned_session),
        "services_started": False,
        "kill_switch_removed": False,
        "production_mutation_performed": False,
        "real_order_submitted": False,
        "renewal_of_existing_unactivated_authorization": (
            args.renew_existing_unactivated
        ),
        "replacement_of_completed_cleaned_session": (
            args.replace_completed_cleaned_session
        ),
        "replacement_of_expired_cleaned_zero_attempt_session": (
            args.replace_expired_cleaned_zero_attempt_session
        ),
        "previous_authorization_id": (
            str(
                (
                    state.get("phase_15_v3_live_canary") or {}
                ).get("fast_live_preauthorization", {}).get("authorization_id") or ""
            )
            if (
                args.replace_completed_cleaned_session
                or args.replace_expired_cleaned_zero_attempt_session
            )
            else ""
        ),
        "completed_session_cleanup_evidence": cleanup_evidence_reference,
        "completed_session_cleanup_evidence_sha256": (
            _raw_sha256(cleanup_path) if cleanup_path is not None else ""
        ),
        "zero_attempt_cleanup_evidence": (
            zero_attempt_cleanup_evidence_reference
        ),
        "zero_attempt_cleanup_evidence_sha256": (
            _raw_sha256(zero_attempt_cleanup_path)
            if zero_attempt_cleanup_path is not None
            else ""
        ),
    }

    _write_new_json(output, candidate)
    try:
        _write_new_json(evidence_output, candidate_evidence)
    except Exception:
        output.unlink(missing_ok=True)
        raise

    print("PHASE15_FAST_LIVE_AUTO_AUTHORIZATION_CANDIDATE=PASS")
    print(f"EXPECTED_MAIN={args.expected_main}")
    print(f"AUTHORIZATION_ID={args.authorization_id}")
    print(f"AUTHORIZATION_MODE={FAST_LIVE_CONTINUOUS_AUTO_AUTHORIZATION_MODE_V2}")
    print(f"SOURCE_OF_TRUTH_VERSION={args.source_of_truth_version}")
    print(f"EXPIRES_AT={_utc(expires).isoformat()}")
    print(f"CANDIDATE_PROJECT_STATE={output}")
    print(f"CANDIDATE_EVIDENCE={evidence_output}")
    print(
        "RENEWAL_OF_EXISTING_UNACTIVATED_AUTHORIZATION="
        + ("true" if args.renew_existing_unactivated else "false")
    )
    print(
        "REPLACEMENT_OF_COMPLETED_CLEANED_SESSION="
        + ("true" if args.replace_completed_cleaned_session else "false")
    )
    print(
        "REPLACEMENT_OF_EXPIRED_CLEANED_ZERO_ATTEMPT_SESSION="
        + (
            "true"
            if args.replace_expired_cleaned_zero_attempt_session
            else "false"
        )
    )
    print("AUTO_APPROVAL_REMAINS_AUTHORIZED=true")
    print("GLOBAL_LIVE_TRADING_ENABLED=false")
    print("PHASE_LIVE_TRADING_ENABLED=false")
    print("RUNTIME_AUTHORIZATION_CREATED=false")
    print("PRODUCTION_MUTATION_PERFORMED=false")
    print("REAL_ORDER_SUBMITTED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
