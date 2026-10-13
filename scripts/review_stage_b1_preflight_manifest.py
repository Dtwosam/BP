"""Fail-closed OFFLINE Stage B1 source-patch preflight manifest checker.

No VM/SSH/systemd/network/DB/process access, production launcher or mutation.
This checks ONLY schema/claimed values and an optional LOCAL patch's exact
bytes. Self-reported host evidence is never independently authenticated.
Even a complete result is ALWAYS PRODUCTION_NO_GO without separate review
and an explicit fresh authorization outside this tool.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Works both as `python scripts/...` and when imported by pytest.
if __package__:
    from scripts.rehearse_stage_b1_backport import BASE_COMMIT, FILES, TARGET_COMMIT
else:
    from rehearse_stage_b1_backport import BASE_COMMIT, FILES, TARGET_COMMIT

EXPECTED_PATCH_SHA256 = (
    "e0a15b1f890233fa6b1228695170f64967b3e9c2899580951242b78ef7ea9b10"
)
EXPECTED_PATCH_BYTES = 20_045
MAX_MANIFEST_BYTES = 8192
MAX_LOCAL_PATCH_BYTES = 65536
REPORT_SCHEMA = "phase14_b1_source_only_offline_preflight_v1"

_CANDIDATE_KEYS = frozenset({
    "baseline_commit", "source_target_commit", "source_patch_sha256",
    "source_patch_bytes", "file_blobs", "includes_one_shot_launcher",
    "includes_process_supervisor",
})
_HOST_KEYS = frozenset({
    "observed_deployed_commit", "service_unit_sha256",
    "python_executable_sha256", "recorder_service_pid",
    "recorder_start_utc", "recorder_restart_count",
    "recorder_health_verified", "mode", "live_trading_enabled",
    "max_trade_size_usd", "max_daily_loss_usd", "max_total_exposure_usd",
    "wallet_credentials_accessible", "auto_promotion_enabled",
})
_ROLLBACK_KEYS = frozenset({
    "previous_commit", "previous_tree_sha256", "rollback_bundle_sha256",
    "service_unit_restore_sha256", "restore_rehearsal_evidence_sha256",
    "restore_rehearsed",
})
_CONTROLS_KEYS = frozenset({
    "proposed_duration_seconds", "approved_output_contract_sha256",
    "independent_process_tree_timeout_tested",
    "representative_overhead_review_accepted", "stop_owner_acknowledged",
    "queries_allowed",
})
_CHECKS = (
    "manifest_shape",
    "candidate_source_identity",
    "local_source_patch_bytes",
    "on_host_commit_attestation",
    "runtime_identity_attestation",
    "research_zero_money_attestation",
    "rollback_material_attestation",
    "restore_rehearsal_attestation",
    "one_shot_limits_attestation",
    "independent_isolation_and_overhead_attestation",
)


def _section(value: object, keys: frozenset[str]) -> dict[str, Any] | None:
    if not isinstance(value, dict) or set(value) != keys:
        return None
    return value


def _sha256(value: object) -> bool:
    return (
        type(value) is str and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _utc(value: object) -> bool:
    if type(value) is not str or len(value) > 40:
        return False
    try:
        timestamp = datetime.fromisoformat(value)
        return timestamp.utcoffset() == timedelta(0)
    except ValueError:
        return False


def _nonnegative_integer(value: object) -> bool:
    return type(value) is int and value >= 0


def _zero_money(value: object) -> bool:
    return (
        type(value) in (int, float)
        and math.isfinite(value)
        and value == 0
    )


def _fixed_file_blobs() -> list[dict[str, str | None]]:
    return [
        {
            "path": path,
            "before_git_blob_sha1": before,
            "after_git_blob_sha1": after,
        }
        for path, before, after in FILES
    ]


def empty_template() -> dict[str, object]:
    """Incomplete and non-authorizing by design; never fills host evidence."""
    return {
        "schema": REPORT_SCHEMA,
        "candidate": {
            "baseline_commit": BASE_COMMIT,
            "source_target_commit": TARGET_COMMIT,
            "source_patch_sha256": EXPECTED_PATCH_SHA256,
            "source_patch_bytes": EXPECTED_PATCH_BYTES,
            "file_blobs": _fixed_file_blobs(),
            "includes_one_shot_launcher": False,
            "includes_process_supervisor": False,
        },
        "operator_evidence": dict.fromkeys(_HOST_KEYS),
        "rollback_evidence": dict.fromkeys(_ROLLBACK_KEYS),
        "observation_controls": dict.fromkeys(_CONTROLS_KEYS),
    }


def evaluate_manifest(
    manifest: object, *, local_patch: bytes | None = None
) -> dict[str, object]:
    """Validate claims only; output contains no user-supplied fields or values."""
    root = _section(
        manifest, frozenset({
            "schema", "candidate", "operator_evidence",
            "rollback_evidence", "observation_controls",
        })
    )
    shape = (
        root is not None
        and root["schema"] == REPORT_SCHEMA
        and _section(root["candidate"], _CANDIDATE_KEYS) is not None
        and _section(root["operator_evidence"], _HOST_KEYS) is not None
        and _section(root["rollback_evidence"], _ROLLBACK_KEYS) is not None
        and _section(root["observation_controls"], _CONTROLS_KEYS) is not None
    )
    checks = dict.fromkeys(_CHECKS, False)
    checks["manifest_shape"] = bool(shape)
    if shape and root is not None:
        candidate = root["candidate"]
        host = root["operator_evidence"]
        rollback = root["rollback_evidence"]
        controls = root["observation_controls"]
        checks["candidate_source_identity"] = (
            candidate == empty_template()["candidate"]
        )
        checks["on_host_commit_attestation"] = (
            host["observed_deployed_commit"] == BASE_COMMIT
        )
        checks["runtime_identity_attestation"] = (
            _sha256(host["service_unit_sha256"])
            and _sha256(host["python_executable_sha256"])
            and type(host["recorder_service_pid"]) is int
            and host["recorder_service_pid"] > 1
            and _utc(host["recorder_start_utc"])
            and _nonnegative_integer(host["recorder_restart_count"])
            and host["recorder_health_verified"] is True
        )
        checks["research_zero_money_attestation"] = (
            host["mode"] == "research"
            and host["live_trading_enabled"] is False
            and _zero_money(host["max_trade_size_usd"])
            and _zero_money(host["max_daily_loss_usd"])
            and _zero_money(host["max_total_exposure_usd"])
            and host["wallet_credentials_accessible"] is False
            and host["auto_promotion_enabled"] is False
        )
        checks["rollback_material_attestation"] = (
            rollback["previous_commit"] == BASE_COMMIT
            and _sha256(rollback["previous_tree_sha256"])
            and _sha256(rollback["rollback_bundle_sha256"])
            and _sha256(rollback["service_unit_restore_sha256"])
        )
        checks["restore_rehearsal_attestation"] = (
            rollback["restore_rehearsed"] is True
            and _sha256(rollback["restore_rehearsal_evidence_sha256"])
        )
        checks["one_shot_limits_attestation"] = (
            type(controls["proposed_duration_seconds"]) is int
            and 1 <= controls["proposed_duration_seconds"] <= 120
            and type(controls["queries_allowed"]) is int
            and controls["queries_allowed"] == 0
            and _sha256(controls["approved_output_contract_sha256"])
            and controls["stop_owner_acknowledged"] is True
        )
        checks["independent_isolation_and_overhead_attestation"] = (
            controls["independent_process_tree_timeout_tested"] is True
            and controls["representative_overhead_review_accepted"] is True
        )

    checks["local_source_patch_bytes"] = (
        local_patch is not None
        and type(local_patch) is bytes
        and len(local_patch) == EXPECTED_PATCH_BYTES
        and hashlib.sha256(local_patch).hexdigest() == EXPECTED_PATCH_SHA256
    )
    complete = all(checks.values())
    return {
        "report": REPORT_SCHEMA,
        "status": (
            "STRUCTURE_COMPLETE_UNVERIFIED_NO_GO"
            if complete else "INCOMPLETE_OR_MISMATCHED_NO_GO"
        ),
        "production_execution_authorized": False,
        "production_deploy_or_restart_permitted": False,
        "host_observation_performed": False,
        "host_claims_independently_verified": False,
        "candidate_scope": "pre_608_three_file_source_only",
        "includes_production_one_shot_runner": False,
        "checks": checks,
        "missing_or_mismatched_check_names": [
            key for key in _CHECKS if not checks[key]
        ],
        "limitations": [
            "Input claims are untrusted; matching formats do not prove on-host state.",
            "Even full structure is never a GO or permission to run a diagnostic.",
            "Legacy pinned source patch cannot activate a one-shot collector.",
            "No production access, secrets, source payloads or files are disclosed.",
        ],
    }


def _read_bounded(path: Path, bound: int) -> bytes | None:
    try:
        with path.open("rb") as stream:
            payload = stream.read(bound + 1)
        return payload if len(payload) <= bound else None
    except OSError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--local-source-patch", type=Path,
        help="Optional local-only pinned 20,045-byte source patch; never executed",
    )
    args = parser.parse_args()
    raw = _read_bounded(args.manifest, MAX_MANIFEST_BYTES)
    try:
        manifest = json.loads(raw) if raw is not None else None
    except (UnicodeDecodeError, ValueError):
        manifest = None
    patch = (
        _read_bounded(args.local_source_patch, MAX_LOCAL_PATCH_BYTES)
        if args.local_source_patch is not None
        else None
    )
    report = evaluate_manifest(manifest, local_patch=patch)
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["status"] == "STRUCTURE_COMPLETE_UNVERIFIED_NO_GO" else 2


if __name__ == "__main__":
    raise SystemExit(main())
