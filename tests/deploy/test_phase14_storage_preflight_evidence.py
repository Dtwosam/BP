from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.deploy.verify_phase14_storage_preflight import (
    PreflightVerificationError,
    verify_preflight_transcript,
)

ROOT = Path(__file__).resolve().parents[2]
VERIFIER = ROOT / "scripts" / "deploy" / "verify_phase14_storage_preflight.py"
PREFLIGHT = ROOT / "scripts" / "deploy" / "phase14_partitioned_storage_preflight_cloudshell.sh"
CI = ROOT / ".github" / "workflows" / "ci.yml"

FROM_HEAD = "c29fe227f959305f67031e922ca659869a826c4f"
HEAD = "a8bf5b5c68e8556a65f091ae4e14b677d24650f7"
GIB = 1024**3
ARCHIVE_SHA256 = "a" * 64


def _transcript(
    *,
    dedicated_free_bytes: int = 59 * GIB,
    raw_total_bytes: int = 31 * GIB,
    raw_partitioned: str = "f",
    legacy_table_present: str = "f",
    dedupe_table_present: str = "f",
    mutations_performed: str = "false",
) -> str:
    return f"""PROJECT=project-4397f2c0-7098-4c1c-abb
VM=bp-recorder
ZONE=us-east1-c
FROM_HEAD={FROM_HEAD}
HEAD={HEAD}
MIN_FREE_GIB=40
Running read-only Phase 14 partitioned-storage production preflight.
PHASE14_PARTITIONED_STORAGE_PREFLIGHT=PASS
FROM_HEAD={FROM_HEAD}
FROM_BRANCH=main
HEAD={HEAD}
REMOTE_HEAD={HEAD}
RECORDER_STATE=stopped
POSTGRES_DATA_SOURCE=/var/lib/docker/volumes/bp_bp_postgres_data/_data
DEDICATED_DATA_FREE_BYTES={dedicated_free_bytes}
ROOT_FREE_BYTES={177 * GIB}
ARCHIVE_EVIDENCE=/mnt/bp-data/evidence/phase14-storage-recovery-24-48h-20260904T015955Z.json
ARCHIVE_EVIDENCE_SHA256={ARCHIVE_SHA256}
ARCHIVE_WINDOW_END=2026-09-03T00:00:00+00:00
RAW_TOTAL_BYTES={raw_total_bytes}
RAW_TOTAL_PRETTY=31 GB
RAW_ESTIMATED_ROWS=24482850
RAW_PARTITIONED={raw_partitioned}
LEGACY_TABLE_PRESENT={legacy_table_present}
DEDUPE_TABLE_PRESENT={dedupe_table_present}
MAINTENANCE_TIMER_STATE=inactive
DISK_HEALTH_TIMER_STATE=inactive
MUTATIONS_PERFORMED={mutations_performed}
"""


def _replace_last_field(transcript: str, field: str, value: str) -> str:
    lines = transcript.splitlines()
    matches = [index for index, line in enumerate(lines) if line.startswith(f"{field}=")]
    assert matches, field
    lines[matches[-1]] = f"{field}={value}"
    return "\n".join(lines) + "\n"


def test_verify_preflight_transcript_accepts_expected_legacy_recovery_shape() -> None:
    report = verify_preflight_transcript(
        _transcript(),
        expected_from_head=FROM_HEAD,
        expected_head=HEAD,
        min_free_gib=40,
        critical_reserve_gib=15,
    )

    assert report["verdict"] == "PASS"
    assert report["mutations_performed"] is False
    assert report["recorder_state"] == "stopped"
    assert report["storage_shape"] == "legacy_unmigrated"
    assert report["headroom"]["minimum_free_gib"] == 40
    assert report["headroom"]["critical_reserve_gib"] == 15
    assert report["headroom"]["required_free_bytes"] == 46 * GIB
    assert report["headroom"]["free_bytes"] == 59 * GIB
    assert report["archive"]["window_end"] == "2026-09-03T00:00:00+00:00"
    assert report["target"] == {
        "project": "project-4397f2c0-7098-4c1c-abb",
        "zone": "us-east1-c",
        "vm": "bp-recorder",
    }
    assert report["archive"]["sha256"] == ARCHIVE_SHA256
    assert "raw_total_pretty" not in report


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("FROM_HEAD", "0" * 40, "conflicting FROM_HEAD"),
        ("HEAD", "1" * 40, "conflicting HEAD"),
        ("REMOTE_HEAD", "2" * 40, "unexpected REMOTE_HEAD"),
        ("RECORDER_STATE", "active", "recorder is not stopped"),
        ("RAW_PARTITIONED", "t", "raw storage is already partitioned"),
        ("LEGACY_TABLE_PRESENT", "t", "rollback legacy table already exists"),
        ("DEDUPE_TABLE_PRESENT", "t", "dedupe ledger already exists"),
        ("MUTATIONS_PERFORMED", "true", "preflight reported production mutations"),
    ],
)
def test_verify_preflight_transcript_rejects_boundary_violations(
    field: str,
    value: str,
    reason: str,
) -> None:
    transcript = _replace_last_field(_transcript(), field, value)

    with pytest.raises(PreflightVerificationError, match=reason):
        verify_preflight_transcript(
            transcript,
            expected_from_head=FROM_HEAD,
            expected_head=HEAD,
            min_free_gib=40,
            critical_reserve_gib=15,
        )


def test_verify_preflight_transcript_rejects_conflicting_duplicate_head_fields() -> None:
    transcript = _transcript().replace(
        f"FROM_HEAD={FROM_HEAD}\nHEAD={HEAD}\nMIN_FREE_GIB=40",
        f"FROM_HEAD={'0' * 40}\nHEAD={HEAD}\nMIN_FREE_GIB=40",
        1,
    )

    with pytest.raises(PreflightVerificationError, match="conflicting FROM_HEAD"):
        verify_preflight_transcript(
            transcript,
            expected_from_head=FROM_HEAD,
            expected_head=HEAD,
        )


def test_verify_preflight_transcript_rejects_insufficient_duplication_headroom() -> None:
    with pytest.raises(
        PreflightVerificationError,
        match="insufficient migration headroom",
    ):
        verify_preflight_transcript(
            _transcript(
                dedicated_free_bytes=45 * GIB,
                raw_total_bytes=31 * GIB,
            ),
            expected_from_head=FROM_HEAD,
            expected_head=HEAD,
            min_free_gib=40,
            critical_reserve_gib=15,
        )


def test_verify_preflight_transcript_requires_canonical_recovery_archive_path() -> None:
    transcript = _transcript().replace(
        "/mnt/bp-data/evidence/phase14-storage-recovery-24-48h-20260904T015955Z.json",
        "/tmp/recovery.json",
    )

    with pytest.raises(PreflightVerificationError, match="archive evidence path"):
        verify_preflight_transcript(
            transcript,
            expected_from_head=FROM_HEAD,
            expected_head=HEAD,
        )


def test_verify_preflight_cli_emits_sanitized_json(tmp_path: Path) -> None:
    transcript = tmp_path / "preflight.txt"
    transcript.write_text(_transcript(), encoding="utf-8")

    completed = subprocess.run(
        [
            "python",
            str(VERIFIER),
            "--input",
            str(transcript),
            "--expected-from-head",
            FROM_HEAD,
            "--expected-head",
            HEAD,
            "--expected-project",
            "project-4397f2c0-7098-4c1c-abb",
            "--expected-zone",
            "us-east1-c",
            "--expected-vm",
            "bp-recorder",
            "--expected-archive-evidence",
            (
                "/mnt/bp-data/evidence/"
                "phase14-storage-recovery-24-48h-20260904T015955Z.json"
            ),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["verdict"] == "PASS"
    assert payload["mutations_performed"] is False
    serialized = json.dumps(payload)
    assert "POSTGRES_DATA_SOURCE" not in serialized
    assert "RAW_TOTAL_PRETTY" not in serialized


def test_preflight_helper_enforces_dynamic_headroom_and_unmigrated_shape() -> None:
    content = PREFLIGHT.read_text(encoding="utf-8")

    for marker in (
        "verify_migration_headroom",
        "RAW_TOTAL_BYTES",
        "critical_reserve_bytes",
        "raw_total_bytes + critical_reserve_bytes",
        "insufficient_migration_headroom",
        "verify_unmigrated_storage_shape",
        '"$RAW_PARTITIONED" == "f"',
        '"$LEGACY_TABLE_PRESENT" == "f"',
        '"$DEDUPE_TABLE_PRESENT" == "f"',
    ):
        assert marker in content


def test_preflight_verifier_has_ci_syntax_validation() -> None:
    ci = CI.read_text(encoding="utf-8")
    assert "python -m py_compile scripts/deploy/verify_phase14_storage_preflight.py" in ci

def test_preflight_captures_archive_evidence_sha256_read_only() -> None:
    content = PREFLIGHT.read_text(encoding="utf-8")

    assert 'ARCHIVE_EVIDENCE_SHA256=$(sha256sum "$ARCHIVE_EVIDENCE"' in content
    assert 'echo "ARCHIVE_EVIDENCE_SHA256=$ARCHIVE_EVIDENCE_SHA256"' in content


def test_verify_preflight_transcript_rejects_invalid_archive_sha256() -> None:
    transcript = _replace_last_field(
        _transcript(), "ARCHIVE_EVIDENCE_SHA256", "not-a-digest"
    )

    with pytest.raises(
        PreflightVerificationError, match="archive evidence SHA-256 is invalid"
    ):
        verify_preflight_transcript(
            transcript,
            expected_from_head=FROM_HEAD,
            expected_head=HEAD,
        )


def test_verified_preflight_binds_expected_target_identity() -> None:
    content = VERIFIER.read_text(encoding="utf-8")

    for marker in (
        "--expected-project",
        "--expected-zone",
        "--expected-vm",
        "unexpected PROJECT",
        "unexpected ZONE",
        "unexpected VM",
    ):
        assert marker in content

    transcript = _transcript()
    with pytest.raises(PreflightVerificationError, match="unexpected PROJECT"):
        verify_preflight_transcript(
            transcript,
            expected_from_head=FROM_HEAD,
            expected_head=HEAD,
            expected_project="wrong-project",
            expected_zone="us-east1-c",
            expected_vm="bp-recorder",
        )


def test_verified_preflight_binds_expected_archive_evidence_path() -> None:
    content = VERIFIER.read_text(encoding="utf-8")

    for marker in (
        "--expected-archive-evidence",
        "unexpected ARCHIVE_EVIDENCE",
    ):
        assert marker in content

    with pytest.raises(
        PreflightVerificationError,
        match="unexpected ARCHIVE_EVIDENCE",
    ):
        verify_preflight_transcript(
            _transcript(),
            expected_from_head=FROM_HEAD,
            expected_head=HEAD,
            expected_archive_evidence=(
                "/mnt/bp-data/evidence/"
                "phase14-storage-recovery-24-48h-20260904T020000Z.json"
            ),
        )


def test_verified_preflight_binds_configured_min_free_gib() -> None:
    with pytest.raises(
        PreflightVerificationError,
        match="unexpected MIN_FREE_GIB",
    ):
        verify_preflight_transcript(
            _transcript(),
            expected_from_head=FROM_HEAD,
            expected_head=HEAD,
            min_free_gib=41,
        )


def test_verified_preflight_binds_expected_environment_file() -> None:
    verifier = VERIFIER.read_text(encoding="utf-8")
    preflight = PREFLIGHT.read_text(encoding="utf-8")

    assert "--expected-env-file" in verifier
    assert "unexpected ENV_FILE" in verifier
    assert 'echo "ENV_FILE=$ENV_FILE"' in preflight

    transcript = _transcript().replace(
        "MIN_FREE_GIB=40\n",
        "MIN_FREE_GIB=40\nENV_FILE=/etc/bp/bp.env\n",
        1,
    )
    with pytest.raises(PreflightVerificationError, match="unexpected ENV_FILE"):
        verify_preflight_transcript(
            transcript,
            expected_from_head=FROM_HEAD,
            expected_head=HEAD,
            expected_env_file="/etc/bp/other.env",
        )
