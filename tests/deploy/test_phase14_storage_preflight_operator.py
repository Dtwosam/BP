import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "deploy" / "phase14_storage_preflight_evidence_cloudshell.sh"
PREFLIGHT = ROOT / "scripts" / "deploy" / "phase14_partitioned_storage_preflight_cloudshell.sh"
CI = ROOT / ".github" / "workflows" / "ci.yml"


def test_cloudshell_evidence_runner_captures_and_verifies_without_host_mutation() -> None:
    assert RUNNER.is_file(), RUNNER
    content = RUNNER.read_text(encoding="utf-8")

    for marker in (
        "set -Eeuo pipefail",
        "umask 077",
        "PHASE14_PARTITIONED_STORAGE_FROM_HEAD",
        "PHASE14_PARTITIONED_STORAGE_HEAD",
        "phase14_partitioned_storage_preflight_cloudshell.sh",
        "verify_phase14_storage_preflight.py",
        'tee -a "$TRANSCRIPT"',
        '--expected-from-head "$EXPECTED_FROM_HEAD"',
        '--expected-head "$EXPECTED_HEAD"',
        "PHASE14_STORAGE_PREFLIGHT_EVIDENCE=PASS",
        "TRANSCRIPT=",
        "VERIFIED=",
    ):
        assert marker in content

    lowered = content.lower()
    for forbidden in (
        "systemd-run",
        "systemctl stop",
        "systemctl start",
        "systemctl restart",
        "git checkout",
        "git reset",
        "migrate_partitioned_raw_storage.py",
        "storage_maintenance.py run",
        "docker compose",
        "psql ",
    ):
        assert forbidden not in lowered


def test_cloudshell_evidence_runner_has_clean_bash_syntax_and_ci_validation() -> None:
    shell = subprocess.run(
        ["bash", "-n", str(RUNNER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert shell.returncode == 0, shell.stderr

    ci = CI.read_text(encoding="utf-8")
    assert "bash -n scripts/deploy/phase14_storage_preflight_evidence_cloudshell.sh" in ci


def test_read_only_preflight_honors_configured_postgres_identity() -> None:
    content = PREFLIGHT.read_text(encoding="utf-8")

    for marker in (
        'postgres_user=$(read_env POSTGRES_USER)',
        'postgres_db=$(read_env POSTGRES_DB)',
        'postgres_user=${postgres_user:-bp}',
        'postgres_db=${postgres_db:-bp}',
        'psql -U "$postgres_user" -d "$postgres_db"',
    ):
        assert marker in content

    assert "psql -U bp -d bp" not in content


def test_read_only_preflight_clamps_unknown_reltuples_estimate() -> None:
    content = PREFLIGHT.read_text(encoding="utf-8")

    assert "GREATEST(" in content
    assert "reltuples::bigint" in content
    assert "), 0)" in content

def test_cloudshell_evidence_runner_binds_local_files_to_exact_candidate() -> None:
    content = RUNNER.read_text(encoding="utf-8")

    for marker in (
        'LOCAL_HEAD=$(git rev-parse HEAD)',
        '[[ "$LOCAL_HEAD" == "$EXPECTED_HEAD" ]]',
        'git status --porcelain --untracked-files=all',
        "local_candidate_head_mismatch",
        "local_working_tree_dirty",
    ):
        assert marker in content

def test_cloudshell_evidence_runner_binds_preflight_to_state_archive_path() -> None:
    content = RUNNER.read_text(encoding="utf-8")

    for marker in (
        "PROJECT_STATE.json",
        "archive_recovery_host_evidence",
        "PHASE14_PARTITIONED_STORAGE_ARCHIVE_EVIDENCE",
        "archive_evidence_binding_invalid",
    ):
        assert marker in content

    assert 'PHASE14_PARTITIONED_STORAGE_ARCHIVE_EVIDENCE="$ARCHIVE_EVIDENCE"' in content

def test_cloudshell_evidence_runner_reports_verified_digest_without_approval() -> None:
    content = RUNNER.read_text(encoding="utf-8")

    for marker in (
        'VERIFIED_SHA256=$(sha256sum "$VERIFIED"',
        'echo "VERIFIED_SHA256=$VERIFIED_SHA256"',
        "verified_preflight_digest_invalid",
    ):
        assert marker in content

    assert "PHASE14_PARTITIONED_STORAGE_APPROVED_PREFLIGHT_SHA256=" not in content


def test_cloudshell_evidence_runner_reserves_local_evidence_paths_without_clobber() -> None:
    content = RUNNER.read_text(encoding="utf-8")

    for marker in (
        '[[ "$TRANSCRIPT" != "$VERIFIED" ]]',
        "preflight_evidence_paths_must_differ",
        '(set -o noclobber; : > "$TRANSCRIPT")',
        "preflight_transcript_path_exists",
        'tee -a "$TRANSCRIPT"',
        '(set -o noclobber; : > "$VERIFIED")',
        "preflight_verified_path_exists",
        'rm -f "$VERIFIED"',
    ):
        assert marker in content

    transcript = content.index('TRANSCRIPT="${PHASE14_STORAGE_PREFLIGHT_TRANSCRIPT:-')
    verified = content.index('VERIFIED="${PHASE14_STORAGE_PREFLIGHT_VERIFIED:-', transcript)
    distinct = content.index('[[ "$TRANSCRIPT" != "$VERIFIED" ]]', verified)
    reserve_transcript = content.index(
        '(set -o noclobber; : > "$TRANSCRIPT")',
        distinct,
    )
    preflight = content.index('bash "$PREFLIGHT" | tee -a "$TRANSCRIPT"', reserve_transcript)
    reserve_verified = content.index(
        '(set -o noclobber; : > "$VERIFIED")',
        preflight,
    )
    verifier = content.index('python "$VERIFIER"', reserve_verified)
    cleanup_failed_verified = content.index('rm -f "$VERIFIED"', verifier)

    assert (
        transcript
        < verified
        < distinct
        < reserve_transcript
        < preflight
        < reserve_verified
        < verifier
        < cleanup_failed_verified
    )
