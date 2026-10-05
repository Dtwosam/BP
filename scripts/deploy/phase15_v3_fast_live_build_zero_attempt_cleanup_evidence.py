from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_PURPOSE = "phase15-v3-fast-live-expired-zero-attempt-cleanup-v1"
_STATUS = "CLEANUP_VERIFIED"
_CLEANUP_MODE = "expired_zero_attempt"
_AUTH_MODE = "auto-telegram-continuous-v2"


class EvidenceError(RuntimeError):
    pass


def _parse_kv_output(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if key in values and values[key] != value:
            raise EvidenceError(f"conflicting cleanup output value: {key}")
        values[key] = value
    return values


def _require(values: dict[str, str], key: str, expected: str) -> None:
    observed = values.get(key)
    if observed != expected:
        raise EvidenceError(
            f"cleanup output mismatch: {key} expected {expected!r}, got {observed!r}"
        )


def _require_present(values: dict[str, str], key: str) -> str:
    value = values.get(key, "")
    if not value:
        raise EvidenceError(f"cleanup output missing: {key}")
    return value


def _require_nonnegative_int(values: dict[str, str], key: str) -> int:
    raw = _require_present(values, key)
    try:
        value = int(raw)
    except ValueError as exc:
        raise EvidenceError(f"cleanup output integer invalid: {key}") from exc
    if value < 0:
        raise EvidenceError(f"cleanup output integer negative: {key}")
    return value


def build_evidence(
    *,
    cleanup_output: str,
    observed_at: datetime,
) -> dict[str, Any]:
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise EvidenceError("observed_at must be timezone-aware")
    observed = observed_at.astimezone(UTC)
    values = _parse_kv_output(cleanup_output)

    exact = {
        "PHASE15_FAST_LIVE_CLEANUP": "PASS",
        "CLEANUP_MODE": _CLEANUP_MODE,
        "AUTHORIZATION_MODE": _AUTH_MODE,
        "ZERO_NETWORK_ATTEMPT_VERIFIED": "true",
        "SESSION_PUBLICATION_COUNT": "0",
        "SESSION_NETWORK_SUBMISSION_ATTEMPT_COUNT": "0",
        "SESSION_EXECUTION_RESULT_COUNT": "0",
        "SESSION_REAL_ORDER_SUBMITTED": "false",
        "KILL_SWITCH_ENGAGED": "true",
        "SESSION_RUNTIME_FILES_PRESENT": "false",
        "SESSION_PUBSUB_RESOURCES_PRESENT": "false",
        "RECORDER_RUNTIME_AUTHORIZATION_PRESENT": "false",
        "EXECUTOR_RUNTIME_AUTHORIZATION_PRESENT": "false",
        "RECORDER_TRANSPORT_KEY_PRESENT": "false",
        "EXECUTOR_TRANSPORT_KEY_PRESENT": "false",
        "RECORDER_SOURCE_ACTIVE": "false",
        "EXECUTOR_RECEIVER_ACTIVE": "false",
        "HISTORICAL_STATE_PRESERVED": "true",
        "CLEANUP_COMPLETED": "true",
        "SERVICES_STARTED": "false",
        "REAL_ORDER_SUBMITTED": "false",
        "EXECUTOR_GEO_COUNTRY": "ZA",
        "EXECUTOR_GEO_BLOCKED": "false",
        "EXECUTOR_OPEN_ORDER_COUNT": "0",
        "EXECUTOR_ACCOUNT_CLEAN": "true",
    }
    for key, expected in exact.items():
        _require(values, key, expected)

    authorization_id = _require_present(values, "AUTHORIZATION_ID")
    session_release_main = _require_present(values, "SESSION_RELEASE_MAIN")
    runtime_expires_at = _require_present(values, "RUNTIME_EXPIRES_AT")
    source_host = _require_present(values, "AUTHORIZATION_SOURCE_HOST")
    if source_host not in {"recorder", "executor"}:
        raise EvidenceError("cleanup output authorization source host invalid")
    if len(session_release_main) != 40 or any(
        ch not in "0123456789abcdef" for ch in session_release_main
    ):
        raise EvidenceError("cleanup output session release main invalid")
    try:
        runtime_expiry = datetime.fromisoformat(runtime_expires_at)
    except ValueError as exc:
        raise EvidenceError("cleanup output runtime expiry invalid") from exc
    if runtime_expiry.tzinfo is None or runtime_expiry.utcoffset() is None:
        raise EvidenceError("cleanup output runtime expiry must be timezone-aware")
    if observed < runtime_expiry.astimezone(UTC):
        raise EvidenceError("cleanup evidence cannot precede runtime expiry")

    publication_count = _require_nonnegative_int(values, "SESSION_PUBLICATION_COUNT")
    if publication_count != 0:
        raise EvidenceError("cleanup output mismatch: SESSION_PUBLICATION_COUNT expected '0'")
    _require_nonnegative_int(values, "SESSION_NETWORK_SUBMISSION_ATTEMPT_COUNT")
    _require_nonnegative_int(values, "SESSION_EXECUTION_RESULT_COUNT")
    _require_nonnegative_int(values, "EXECUTOR_OPEN_ORDER_COUNT")

    return {
        "schema_version": 1,
        "purpose": _PURPOSE,
        "status": _STATUS,
        "observed_at": observed.isoformat(),
        "authorization_id": authorization_id,
        "authorization_mode": _AUTH_MODE,
        "authorization_source_host": source_host,
        "session_release_main": session_release_main,
        "runtime_expires_at": runtime_expiry.astimezone(UTC).isoformat(),
        "cleanup_mode": _CLEANUP_MODE,
        "cleanup_completed": True,
        "zero_network_attempt_verified": True,
        "session_publication_count": publication_count,
        "session_network_submission_attempt_count": 0,
        "session_execution_result_count": 0,
        "session_real_order_submitted": False,
        "services_started": False,
        "cleanup_real_order_submitted": False,
        "kill_switch_engaged": True,
        "historical_state_preserved": True,
        "session_runtime_files_present": False,
        "session_pubsub_resources_present": False,
        "recorder_source_active": False,
        "executor_receiver_active": False,
        "recorder_runtime_authorization_present": False,
        "executor_runtime_authorization_present": False,
        "recorder_transport_key_present": False,
        "executor_transport_key_present": False,
        "executor_account_clean": True,
        "executor_open_order_count": 0,
        "executor_geo_country": "ZA",
        "executor_geo_blocked": False,
    }


def _write_new_json(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        raise EvidenceError(f"output already exists: {path}")
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cleanup-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--observed-at")
    args = parser.parse_args()

    cleanup_path = args.cleanup_output.resolve()
    output_path = args.output.resolve()
    if cleanup_path == output_path:
        raise SystemExit("cleanup output and evidence output must differ")
    try:
        cleanup_text = cleanup_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"cleanup output unreadable: {cleanup_path}") from exc

    observed_at = (
        datetime.fromisoformat(args.observed_at)
        if args.observed_at
        else datetime.now(UTC)
    )
    try:
        evidence = build_evidence(
            cleanup_output=cleanup_text,
            observed_at=observed_at,
        )
        evidence["cleanup_output_sha256"] = hashlib.sha256(
            cleanup_text.encode("utf-8")
        ).hexdigest()
        _write_new_json(output_path, evidence)
    except (EvidenceError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    print("PHASE15_FAST_LIVE_ZERO_ATTEMPT_CLEANUP_EVIDENCE=PASS")
    print(f"AUTHORIZATION_ID={evidence['authorization_id']}")
    print(f"SESSION_RELEASE_MAIN={evidence['session_release_main']}")
    print(f"RUNTIME_EXPIRES_AT={evidence['runtime_expires_at']}")
    print(f"CLEANUP_EVIDENCE={output_path}")
    print("PRODUCTION_MUTATION_PERFORMED=false")
    print("REAL_ORDER_SUBMITTED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
