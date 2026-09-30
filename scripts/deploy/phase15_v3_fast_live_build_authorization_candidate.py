from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bp_engine.execution.fast_live import (
    FAST_LIVE_CONTINUOUS_MANUAL_AUTHORIZATION_MODE_V2,
    FAST_LIVE_EXECUTION_VERSION,
    FAST_LIVE_MAX_TRANSIT_SECONDS,
    FAST_LIVE_PREDICTION_VERSION,
    project_state_sha256,
    verify_source_authorization,
)

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_ACCEPT = "I_ACCEPT_GENERATE_REVIEWABLE_CONTINUOUS_LIVE_AUTHORIZATION_CANDIDATE"
_DEACTIVATION_PURPOSE = "phase15-v3-telegram-auto-approver-deactivation-v1"
_CANDIDATE_PURPOSE = "phase15-v3-fast-live-continuous-authorization-candidate-v2"


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
    deactivation: dict[str, Any],
    expected_main: str,
    authorization_id: str,
    source_of_truth_version: str,
    expires_at: datetime,
    authorized_at: datetime,
    deactivation_evidence_reference: str,
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
    if not deactivation_evidence_reference.strip():
        raise CandidateError("deactivation evidence reference missing")

    if deactivation.get("schema_version") != 1:
        raise CandidateError("deactivation evidence schema invalid")
    if deactivation.get("purpose") != _DEACTIVATION_PURPOSE:
        raise CandidateError("deactivation evidence purpose invalid")
    if deactivation.get("status") != "DEACTIVATED_VERIFIED":
        raise CandidateError("deactivation evidence status invalid")
    if deactivation.get("repository_main") != expected_main:
        raise CandidateError("deactivation evidence main mismatch")
    required_false = (
        "service_active_after",
        "service_enabled_or_loaded_after",
        "matching_process_present_after",
        "live_auto_approve_runtime_effective_after",
        "project_state_mutated",
        "live_trading_enabled",
        "real_order_submitted",
    )
    for name in required_false:
        if deactivation.get(name) is not False:
            raise CandidateError(f"deactivation evidence unsafe: {name}")
    if (
        deactivation.get("source_truth_live_auto_approve_authorized_after")
        is not True
    ):
        raise CandidateError(
            "deactivation evidence must precede source-truth mutation"
        )
    if deactivation.get("telegram_session_deleted") is not False:
        raise CandidateError("deactivation evidence deleted Telegram session")
    if deactivation.get("telegram_credentials_mutated") is not False:
        raise CandidateError(
            "deactivation evidence mutated Telegram credentials"
        )
    deactivated_at = _utc(
        datetime.fromisoformat(str(deactivation.get("observed_at") or ""))
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
        raise CandidateError(
            "source truth must still record the legacy auto-approver as active"
        )
    if phase.get("fast_live_preauthorization") is not None:
        raise CandidateError("fast live preauthorization already exists")
    if state.get("live_trading_enabled") is not False:
        raise CandidateError("global live trading flag must remain false")
    if phase.get("live_trading_enabled") is not False:
        raise CandidateError("phase live trading flag must remain false")

    candidate = copy.deepcopy(state)
    candidate["source_of_truth_version"] = source_of_truth_version
    candidate["updated_at"] = authorized.date().isoformat()
    candidate["current_phase_name"] = (
        "controlled live launch — continuous Telegram-approved fast-live candidate"
    )
    candidate["status"] = "PHASE_15_CONTINUOUS_FAST_LIVE_AUTHORIZED_NOT_ACTIVATED"

    candidate_phase = candidate["phase_15_v3_live_canary"]
    candidate_phase["status"] = "CONTINUOUS_FAST_LIVE_AUTHORIZED_NOT_ACTIVATED"

    candidate_auto = candidate_phase["operator_telegram_auto_approver"]
    candidate_auto["status"] = "DEACTIVATED_FOR_MANUAL_TELEGRAM_CONTINUOUS_LIVE"
    candidate_auto["live_auto_approve_authorized"] = False
    candidate_auto["service_state"] = "stopped"
    candidate_auto["deactivated_at"] = deactivated_at.isoformat()
    candidate_auto["deactivation_evidence"] = deactivation_evidence_reference
    candidate_auto["continuous_manual_live_replacement_authorized"] = True
    candidate_auto["real_order_submitted_by_deactivation"] = False

    candidate_phase["fast_live_preauthorization"] = {
        "status": "AUTHORIZED_CONTINUOUS_SESSION",
        "authorized": True,
        "authorization_id": authorization_id,
        "authorization_mode": FAST_LIVE_CONTINUOUS_MANUAL_AUTHORIZATION_MODE_V2,
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
        "deactivation_evidence": deactivation_evidence_reference,
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

    # The candidate must satisfy the exact runtime verifier while the global
    # deployment/live flags remain false.
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
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-state", type=Path, required=True)
    parser.add_argument("--deactivation-evidence", type=Path, required=True)
    parser.add_argument("--expected-main", required=True)
    parser.add_argument("--authorization-id", required=True)
    parser.add_argument("--source-of-truth-version", required=True)
    parser.add_argument("--expires-at", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence-output", type=Path, required=True)
    parser.add_argument("--accept-candidate", required=True)
    args = parser.parse_args()

    if args.accept_candidate != _ACCEPT:
        raise SystemExit("explicit candidate generation acceptance required")

    project_state = args.project_state.resolve()
    deactivation_path = args.deactivation_evidence.resolve()
    output = args.output.resolve()
    evidence_output = args.evidence_output.resolve()
    if output == project_state:
        raise SystemExit("candidate generator refuses to overwrite PROJECT_STATE")
    if output == evidence_output:
        raise SystemExit("candidate state and evidence outputs must differ")

    state = _load_object(project_state, label="project state")
    deactivation = _load_object(
        deactivation_path,
        label="auto-approver deactivation evidence",
    )
    if deactivation.get("project_state_sha256") != _raw_sha256(project_state):
        raise SystemExit("deactivation evidence project-state hash mismatch")

    try:
        evidence_reference = str(
            deactivation_path.relative_to(project_state.parent)
        )
    except ValueError as exc:
        raise SystemExit(
            "deactivation evidence must be stored under the repository root"
        ) from exc

    now = datetime.now(UTC)
    expires = datetime.fromisoformat(args.expires_at)
    candidate = build_candidate(
        state=state,
        deactivation=deactivation,
        expected_main=args.expected_main,
        authorization_id=args.authorization_id,
        source_of_truth_version=args.source_of_truth_version,
        expires_at=expires,
        authorized_at=now,
        deactivation_evidence_reference=evidence_reference,
    )
    candidate_evidence = {
        "schema_version": 1,
        "purpose": _CANDIDATE_PURPOSE,
        "status": "CANDIDATE_GENERATED_NOT_MERGED_NOT_ACTIVATED",
        "source_main": args.expected_main,
        "source_project_state_sha256": _raw_sha256(project_state),
        "deactivation_evidence": evidence_reference,
        "deactivation_evidence_sha256": _raw_sha256(deactivation_path),
        "authorization_id": args.authorization_id,
        "source_of_truth_version": args.source_of_truth_version,
        "authorized_at": now.isoformat(),
        "expires_at": _utc(expires).isoformat(),
        "candidate_project_state_sha256": project_state_sha256(candidate),
        "global_live_trading_enabled": False,
        "phase_live_trading_enabled": False,
        "runtime_authorization_created": False,
        "release_staged": False,
        "services_started": False,
        "kill_switch_removed": False,
        "production_mutation_performed": False,
        "real_order_submitted": False,
    }

    _write_new_json(output, candidate)
    try:
        _write_new_json(evidence_output, candidate_evidence)
    except Exception:
        output.unlink(missing_ok=True)
        raise

    print("PHASE15_FAST_LIVE_AUTHORIZATION_CANDIDATE=PASS")
    print(f"EXPECTED_MAIN={args.expected_main}")
    print(f"AUTHORIZATION_ID={args.authorization_id}")
    print(f"SOURCE_OF_TRUTH_VERSION={args.source_of_truth_version}")
    print(f"EXPIRES_AT={_utc(expires).isoformat()}")
    print(f"CANDIDATE_PROJECT_STATE={output}")
    print(f"CANDIDATE_EVIDENCE={evidence_output}")
    print("GLOBAL_LIVE_TRADING_ENABLED=false")
    print("PHASE_LIVE_TRADING_ENABLED=false")
    print("RUNTIME_AUTHORIZATION_CREATED=false")
    print("PRODUCTION_MUTATION_PERFORMED=false")
    print("REAL_ORDER_SUBMITTED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
