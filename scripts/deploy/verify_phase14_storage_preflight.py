from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

GIB = 1024**3
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_ARCHIVE_RE = re.compile(
    r"^/mnt/bp-data/evidence/"
    r"phase14-storage-recovery-24-48h-[0-9]{8}T[0-9]{6}Z\.json$"
)


class PreflightVerificationError(RuntimeError):
    pass


def _parse_transcript(transcript: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in transcript.splitlines():
        line = raw_line.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            normalized = value.strip()
            existing = values.get(key)
            if existing is not None and existing != normalized:
                raise PreflightVerificationError(f"conflicting {key} values")
            values[key] = normalized
    return values


def _required(values: dict[str, str], key: str) -> str:
    value = values.get(key)
    if value is None or value == "":
        raise PreflightVerificationError(f"missing {key}")
    return value


def _require_sha(value: str, *, field: str) -> str:
    if not _SHA_RE.fullmatch(value):
        raise PreflightVerificationError(f"{field} is not an exact 40-character SHA")
    return value


def _integer(values: dict[str, str], key: str, *, minimum: int = 0) -> int:
    raw = _required(values, key)
    try:
        value = int(raw)
    except ValueError as exc:
        raise PreflightVerificationError(f"{key} is not an integer") from exc
    if value < minimum:
        raise PreflightVerificationError(f"{key} is below {minimum}")
    return value


def _postgres_false(values: dict[str, str], key: str) -> bool:
    raw = _required(values, key).lower()
    if raw in {"f", "false"}:
        return False
    if raw in {"t", "true"}:
        return True
    raise PreflightVerificationError(f"{key} is not a PostgreSQL boolean")


def verify_preflight_transcript(
    transcript: str,
    *,
    expected_from_head: str,
    expected_head: str,
    min_free_gib: int = 40,
    critical_reserve_gib: int = 15,
) -> dict[str, Any]:
    _require_sha(expected_from_head, field="expected_from_head")
    _require_sha(expected_head, field="expected_head")
    if min_free_gib < 25:
        raise PreflightVerificationError("min_free_gib must be at least 25")
    if critical_reserve_gib < 15:
        raise PreflightVerificationError(
            "critical_reserve_gib must preserve at least 15 GiB"
        )

    values = _parse_transcript(transcript)
    if _required(values, "PHASE14_PARTITIONED_STORAGE_PREFLIGHT") != "PASS":
        raise PreflightVerificationError("preflight did not report PASS")

    from_head = _require_sha(_required(values, "FROM_HEAD"), field="FROM_HEAD")
    head = _require_sha(_required(values, "HEAD"), field="HEAD")
    remote_head = _require_sha(_required(values, "REMOTE_HEAD"), field="REMOTE_HEAD")

    if from_head != expected_from_head:
        raise PreflightVerificationError("unexpected FROM_HEAD")
    if head != expected_head:
        raise PreflightVerificationError("unexpected HEAD")
    if remote_head != expected_head:
        raise PreflightVerificationError("unexpected REMOTE_HEAD")

    if _required(values, "RECORDER_STATE") != "stopped":
        raise PreflightVerificationError("recorder is not stopped")

    mutations_raw = _required(values, "MUTATIONS_PERFORMED").lower()
    if mutations_raw != "false":
        raise PreflightVerificationError("preflight reported production mutations")

    raw_partitioned = _postgres_false(values, "RAW_PARTITIONED")
    if raw_partitioned:
        raise PreflightVerificationError("raw storage is already partitioned")

    legacy_present = _postgres_false(values, "LEGACY_TABLE_PRESENT")
    if legacy_present:
        raise PreflightVerificationError("rollback legacy table already exists")

    dedupe_present = _postgres_false(values, "DEDUPE_TABLE_PRESENT")
    if dedupe_present:
        raise PreflightVerificationError("dedupe ledger already exists")

    archive_path = _required(values, "ARCHIVE_EVIDENCE")
    if not _ARCHIVE_RE.fullmatch(archive_path):
        raise PreflightVerificationError("archive evidence path is not canonical")

    window_end = _required(values, "ARCHIVE_WINDOW_END")
    try:
        parsed_window_end = datetime.fromisoformat(window_end.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PreflightVerificationError("archive window_end is invalid") from exc
    if parsed_window_end.tzinfo is None or parsed_window_end.utcoffset() is None:
        raise PreflightVerificationError("archive window_end is not timezone-aware")

    free_bytes = _integer(values, "DEDICATED_DATA_FREE_BYTES", minimum=1)
    root_free_bytes = _integer(values, "ROOT_FREE_BYTES", minimum=1)
    raw_total_bytes = _integer(values, "RAW_TOTAL_BYTES", minimum=1)
    raw_estimated_rows = _integer(values, "RAW_ESTIMATED_ROWS", minimum=0)

    configured_minimum_bytes = min_free_gib * GIB
    critical_reserve_bytes = critical_reserve_gib * GIB
    required_free_bytes = max(
        configured_minimum_bytes,
        raw_total_bytes + critical_reserve_bytes,
    )
    if free_bytes < required_free_bytes:
        raise PreflightVerificationError(
            "insufficient migration headroom: "
            f"{free_bytes} < required {required_free_bytes}"
        )

    return {
        "verdict": "PASS",
        "from_head": from_head,
        "head": head,
        "remote_head": remote_head,
        "mutations_performed": False,
        "recorder_state": "stopped",
        "storage_shape": "legacy_unmigrated",
        "headroom": {
            "free_bytes": free_bytes,
            "root_free_bytes": root_free_bytes,
            "raw_total_bytes": raw_total_bytes,
            "minimum_free_gib": min_free_gib,
            "critical_reserve_gib": critical_reserve_gib,
            "required_free_bytes": required_free_bytes,
        },
        "raw": {
            "estimated_rows": raw_estimated_rows,
            "partitioned": False,
            "legacy_table_present": False,
            "dedupe_table_present": False,
        },
        "archive": {
            "evidence_name": Path(archive_path).name,
            "window_end": window_end,
        },
        "timers": {
            "maintenance": values.get("MAINTENANCE_TIMER_STATE"),
            "disk_health": values.get("DISK_HEALTH_TIMER_STATE"),
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify a captured Phase 14 read-only storage preflight transcript"
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--expected-from-head", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--min-free-gib", type=int, default=40)
    parser.add_argument("--critical-reserve-gib", type=int, default=15)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        transcript = Path(args.input).read_text(encoding="utf-8")
        report = verify_preflight_transcript(
            transcript,
            expected_from_head=args.expected_from_head,
            expected_head=args.expected_head,
            min_free_gib=args.min_free_gib,
            critical_reserve_gib=args.critical_reserve_gib,
        )
    except (OSError, PreflightVerificationError) as exc:
        print(f"PHASE14_STORAGE_PREFLIGHT_EVIDENCE=FAIL: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
