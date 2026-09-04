import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts" / "deploy" / "phase14_partitioned_storage_rollout_cloudshell.sh"
MIGRATOR = ROOT / "scripts" / "deploy" / "migrate_partitioned_raw_storage.py"
CI = ROOT / ".github" / "workflows" / "ci.yml"


def test_partitioned_storage_rollout_is_exact_head_detached_and_fail_closed() -> None:
    assert HELPER.is_file(), HELPER
    content = HELPER.read_text(encoding="utf-8")

    required = (
        "set -Eeuo pipefail",
        "PHASE14_PARTITIONED_STORAGE_HEAD",
        "PHASE14_PARTITIONED_STORAGE_FROM_HEAD",
        "exact 40-character verified candidate SHA",
        "gcloud auth list",
        "refs/remotes/origin/$BRANCH",
        '[[ "$REMOTE_HEAD" == "$SHA" ]]',
        '[[ "$OLD_HEAD" == "$EXPECTED_FROM_HEAD" ]]',
        "systemd-run",
        "bp-phase14-partitioned-storage-",
        "Type=oneshot",
        "RECORDER_RESTARTED=false",
        "ROLLBACK_MATERIAL_RETAINED=true",
    )
    for marker in required:
        assert marker in content

    lowered = content.lower()
    for forbidden in (
        "live_trading_enabled=true",
        "place_order",
        "submit_order",
        "private_key",
        "wallet_address",
        "geoblock bypass",
        "phase 15",
    ):
        assert forbidden not in lowered


def test_partitioned_storage_rollout_requires_stopped_recorder_and_dedicated_data_disk() -> None:
    content = HELPER.read_text(encoding="utf-8")

    required = (
        "recorder_must_be_stopped",
        "bp-recorder.service",
        "mountpoint -q /mnt/bp-data",
        "STORAGE_HEALTH_PATH=/mnt/bp-data",
        "STORAGE_ARCHIVE_DIR=/mnt/bp-data/archive/raw",
        "MIN_FREE_GIB",
        "/var/lib/postgresql/data",
        "docker compose",
        "postgres_data_not_on_dedicated_filesystem",
        "phase14-storage-recovery-24-48h-",
        "hours",
        "intervals",
    )
    for marker in required:
        assert marker in content

    assert 'systemctl restart "$RECORDER_UNIT"' not in content
    assert 'systemctl start "$RECORDER_UNIT"' not in content


def test_partitioned_storage_rollout_preserves_research_zero_money_and_rollback() -> None:
    content = HELPER.read_text(encoding="utf-8")

    for expected in (
        '"$mode" != "research"',
        '"$live_trading_enabled" != "false"',
        '"$max_trade_size_usd" != "0"',
        '"$max_daily_loss_usd" != "0"',
        "research_zero_money_boundary_not_satisfied",
        "ROLLBACK_ARMED=1",
        "rollback_partitioned_storage",
        "migrate_partitioned_raw_storage.py rollback",
        "migrate_partitioned_raw_storage.py apply",
        "migrate_partitioned_raw_storage.py verify",
        "raw_market_events_legacy",
        "synthetic",
        "ROLLBACK",
        "EVIDENCE_DIR=/mnt/bp-data/evidence",
        "phase14-partitioned-storage-rollout-",
    ):
        assert expected in content


def test_partitioned_storage_migrator_has_apply_verify_and_rollback_commands() -> None:
    assert MIGRATOR.is_file(), MIGRATOR
    content = MIGRATOR.read_text(encoding="utf-8")

    for marker in (
        'add_parser("apply"',
        'add_parser("verify"',
        'add_parser("rollback"',
        "ensure_partitioned_raw_storage",
        "migrate_existing=True",
        "rollback_partitioned_raw_storage",
        "non_raw_table_counts",
        "synthetic_duplicate_suppressed",
        "synthetic_partition_routing_verified",
    ):
        assert marker in content


def test_partitioned_storage_rollout_assets_have_clean_syntax_and_ci_validation() -> None:
    shell = subprocess.run(
        ["bash", "-n", str(HELPER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert shell.returncode == 0, shell.stderr

    python = subprocess.run(
        ["python", "-m", "py_compile", str(MIGRATOR)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert python.returncode == 0, python.stderr

    ci = CI.read_text(encoding="utf-8")
    assert "bash -n scripts/deploy/phase14_partitioned_storage_rollout_cloudshell.sh" in ci
    assert "python -m py_compile scripts/deploy/migrate_partitioned_raw_storage.py" in ci



def test_partitioned_storage_exact_parity_is_streaming_and_temp_space_bounded() -> None:
    content = MIGRATOR.read_text(encoding="utf-8")

    assert "stream_results" in content
    assert 'progress_label="RAW_PARITY"' in content
    assert 'f"{progress_label}_ROWS_CHECKED={checked}"' in content
    assert "EXCEPT ALL" not in content

def test_partitioned_storage_rollout_requires_verified_preflight_evidence() -> None:
    content = HELPER.read_text(encoding="utf-8")

    for marker in (
        "PHASE14_PARTITIONED_STORAGE_PREFLIGHT_VERIFIED",
        "verified_preflight_missing",
        'payload.get("verdict") != "PASS"',
        'payload.get("from_head") != expected_from_head',
        'payload.get("head") != expected_head',
        'payload.get("remote_head") != expected_head',
        'payload.get("mutations_performed") is not False',
        'payload.get("recorder_state") != "stopped"',
        'payload.get("storage_shape") != "legacy_unmigrated"',
        "PREFLIGHT_VERIFIED_SHA256",
    ):
        assert marker in content


def test_partitioned_storage_rollout_rechecks_dynamic_migration_headroom_before_apply() -> None:
    content = HELPER.read_text(encoding="utf-8")

    for marker in (
        "verify_migration_headroom",
        "pg_total_relation_size('public.raw_market_events')",
        'postgres_user=$(read_env POSTGRES_USER)',
        'postgres_db=$(read_env POSTGRES_DB)',
        "critical_reserve_bytes=$((15 * 1024 * 1024 * 1024))",
        "raw_total_bytes + critical_reserve_bytes",
        "insufficient_migration_headroom",
        "MIGRATION_REQUIRED_FREE_BYTES",
    ):
        assert marker in content

    assert content.index("verify_migration_headroom") < content.index(
        "migrate_partitioned_raw_storage.py apply"
    )

def test_partitioned_storage_rollout_rechecks_unmigrated_shape_before_mutation() -> None:
    content = HELPER.read_text(encoding="utf-8")

    for marker in (
        "verify_unmigrated_storage_shape",
        "pg_partitioned_table",
        "to_regclass('public.raw_market_events_legacy') IS NOT NULL",
        "to_regclass('public.raw_event_dedupe') IS NOT NULL",
        "raw_storage_already_partitioned",
        "rollback_legacy_table_already_present",
        "dedupe_ledger_already_present",
    ):
        assert marker in content

    assert content.count("verify_unmigrated_storage_shape") >= 3

    runtime_start = content.index("trap on_exit EXIT")
    first_check = content.index("verify_unmigrated_storage_shape", runtime_start)
    rollback_arm = content.index("ROLLBACK_ARMED=1", runtime_start)
    assert first_check < rollback_arm

    stopped = content.index("stop_managed_units", rollback_arm)
    second_check = content.index("verify_unmigrated_storage_shape", stopped)
    candidate_checkout = content.index('git -C "$REPO" checkout --detach --force "$SHA"', stopped)
    assert second_check < candidate_checkout

def test_partitioned_storage_rollout_requires_explicit_sha_bound_approval() -> None:
    content = HELPER.read_text(encoding="utf-8")

    for marker in (
        "PHASE14_PARTITIONED_STORAGE_APPROVED_FROM_HEAD",
        "PHASE14_PARTITIONED_STORAGE_APPROVED_HEAD",
        "migration_approval_missing_or_invalid",
        '[[ "$APPROVED_FROM_HEAD" == "$EXPECTED_FROM_HEAD" ]]',
        '[[ "$APPROVED_HEAD" == "$EXPECTED_HEAD" ]]',
    ):
        assert marker in content

    assert content.index("migration_approval_missing_or_invalid") < content.index(
        "gcloud config set project"
    )

def test_partitioned_storage_rollout_requires_partition_retirement_physical_release() -> None:
    content = HELPER.read_text(encoding="utf-8")

    for marker in (
        "partition_relation_bytes",
        'PARTITION_BYTES_BEFORE_MAINTENANCE=$(partition_relation_bytes)',
        'PARTITION_BYTES_AFTER_MAINTENANCE=$(partition_relation_bytes)',
        "nonempty_partition_retirement_not_observed",
        "partition_dedupe_cleanup_mismatch",
        "partition_relation_bytes_not_released",
        "PARTITION_BYTES_RELEASED",
    ):
        assert marker in content

    before = content.index(
        'PARTITION_BYTES_BEFORE_MAINTENANCE=$(partition_relation_bytes)'
    )
    maintenance = content.index('scripts/storage_maintenance.py" run')
    after = content.index(
        'PARTITION_BYTES_AFTER_MAINTENANCE=$(partition_relation_bytes)'
    )
    assert before < maintenance < after

def test_partitioned_storage_rollout_binds_local_helper_to_exact_clean_candidate() -> None:
    content = HELPER.read_text(encoding="utf-8")

    for marker in (
        'ROOT=$(git rev-parse --show-toplevel',
        'LOCAL_HEAD=$(git rev-parse HEAD)',
        '[[ "$LOCAL_HEAD" == "$EXPECTED_HEAD" ]]',
        'git status --porcelain --untracked-files=all',
        "local_candidate_head_mismatch",
        "local_working_tree_dirty",
    ):
        assert marker in content

    assert content.index("local_candidate_head_mismatch") < content.index(
        "gcloud config set project"
    )
    assert content.index("local_working_tree_dirty") < content.index(
        "gcloud config set project"
    )

def test_partitioned_storage_rollout_binds_host_archive_to_verified_preflight() -> None:
    content = HELPER.read_text(encoding="utf-8")

    for marker in (
        'archive.get("evidence_name")',
        'archive.get("window_end")',
        "PREFLIGHT_ARCHIVE_EVIDENCE_NAME",
        "PREFLIGHT_ARCHIVE_WINDOW_END",
        "EXPECTED_ARCHIVE_EVIDENCE_NAME",
        "EXPECTED_ARCHIVE_WINDOW_END",
        'ARCHIVE_EVIDENCE="$EVIDENCE_DIR/$EXPECTED_ARCHIVE_EVIDENCE_NAME"',
        "archive_evidence_binding_mismatch",
    ):
        assert marker in content

    assert 'ls -1t "$EVIDENCE_DIR"/phase14-storage-recovery-24-48h-' not in content

def test_partitioned_storage_rollout_binds_host_archive_bytes_to_verified_preflight() -> None:
    content = HELPER.read_text(encoding="utf-8")

    for marker in (
        'archive.get("sha256")',
        "PREFLIGHT_ARCHIVE_SHA256",
        "EXPECTED_ARCHIVE_SHA256",
        'sha256sum "$ARCHIVE_EVIDENCE"',
        "archive_evidence_digest_mismatch",
        "PHASE14_PARTITIONED_STORAGE_PREFLIGHT_ARCHIVE_SHA256",
    ):
        assert marker in content

    verify_fn = content.index("verify_archive_evidence()")
    digest_check = content.index('sha256sum "$ARCHIVE_EVIDENCE"', verify_fn)
    migration_apply = content.index("migrate_partitioned_raw_storage.py apply")
    assert verify_fn < digest_check < migration_apply

def test_partitioned_storage_rollout_rechecks_archive_after_managed_units_stop() -> None:
    content = HELPER.read_text(encoding="utf-8")

    initial_check = content.index("verify_archive_evidence")
    stop_boundary = content.index("ROLLBACK_ARMED=1\nstop_managed_units")
    second_check = content.index("verify_archive_evidence", stop_boundary)
    migration_apply = content.index("migrate_partitioned_raw_storage.py apply", second_check)

    assert initial_check < stop_boundary < second_check < migration_apply

def test_partitioned_storage_rollout_evidence_records_preflight_archive_binding() -> None:
    content = HELPER.read_text(encoding="utf-8")

    for marker in (
        '"source_archive_evidence_sha256": archive_evidence_sha256',
        '"source_archive_window_end": archive_window_end',
        '"$EXPECTED_ARCHIVE_SHA256"',
        '"$EXPECTED_ARCHIVE_WINDOW_END"',
        "archive_evidence_sha256,",
        "archive_window_end,",
    ):
        assert marker in content

    evidence_builder = content.index('EVIDENCE_TMP=$(mktemp')
    pass_marker = content.index('"verdict": "PASS"', evidence_builder)
    assert evidence_builder < pass_marker

def test_partitioned_storage_rollout_requires_preflight_target_identity_match() -> None:
    content = HELPER.read_text(encoding="utf-8")

    for marker in (
        'target = payload.get("target") or {}',
        'target.get("project") != expected_project',
        'target.get("zone") != expected_zone',
        'target.get("vm") != expected_vm',
        "verified preflight PROJECT mismatch",
        "verified preflight ZONE mismatch",
        "verified preflight VM mismatch",
    ):
        assert marker in content

    first_target_check = content.index("verified preflight PROJECT mismatch")
    assert first_target_check < content.index('gcloud config set project "$PROJECT"')

def test_partitioned_storage_rollout_evidence_records_verified_target_identity() -> None:
    content = HELPER.read_text(encoding="utf-8")

    for marker in (
        'TARGET_PROJECT="${PHASE14_PARTITIONED_STORAGE_PROJECT:?}"',
        'TARGET_ZONE="${PHASE14_PARTITIONED_STORAGE_ZONE:?}"',
        'TARGET_VM="${PHASE14_PARTITIONED_STORAGE_VM:?}"',
        '"project": target_project',
        '"zone": target_zone',
        '"vm": target_vm',
    ):
        assert marker in content

    evidence_builder = content.index('EVIDENCE_TMP=$(mktemp')
    target_block = content.index('"target": {', evidence_builder)
    pass_marker = content.index('"verdict": "PASS"', evidence_builder)
    assert evidence_builder < pass_marker < target_block

def test_partitioned_storage_rollout_hashes_and_validates_preflight_same_snapshot() -> None:
    content = HELPER.read_text(encoding="utf-8")

    for marker in (
        "import hashlib",
        'raw = Path(path).read_bytes()',
        "hashlib.sha256(raw).hexdigest()",
        "payload = json.loads(raw)",
        "PREFLIGHT_VERIFIED_SHA256",
    ):
        assert marker in content

    assert 'PREFLIGHT_VERIFIED_SHA256=$(sha256sum "$PREFLIGHT_VERIFIED"' not in content

def test_partitioned_storage_rollout_binds_approval_to_verified_preflight_digest() -> None:
    content = HELPER.read_text(encoding="utf-8")

    for marker in (
        "PHASE14_PARTITIONED_STORAGE_APPROVED_PREFLIGHT_SHA256",
        "APPROVED_PREFLIGHT_SHA256",
        '[[ "$APPROVED_PREFLIGHT_SHA256" == "$PREFLIGHT_VERIFIED_SHA256" ]]',
        "migration_approval_preflight_mismatch",
    ):
        assert marker in content

    assert content.index("migration_approval_preflight_mismatch") < content.index(
        'gcloud config set project "$PROJECT"'
    )

def test_partitioned_storage_rollout_evidence_records_explicit_approval_scope() -> None:
    content = HELPER.read_text(encoding="utf-8")

    for marker in (
        'APPROVED_FROM_HEAD="${PHASE14_PARTITIONED_STORAGE_APPROVED_FROM_HEAD:?}"',
        'APPROVED_HEAD="${PHASE14_PARTITIONED_STORAGE_APPROVED_HEAD:?}"',
        'APPROVED_PREFLIGHT_SHA256="${PHASE14_PARTITIONED_STORAGE_APPROVED_PREFLIGHT_SHA256:?}"',
        '"approval": {',
        '"from_sha": approved_from_head',
        '"candidate_sha": approved_head',
        '"verified_preflight_sha256": approved_preflight_sha256',
    ):
        assert marker in content

    evidence_builder = content.index('EVIDENCE_TMP=$(mktemp')
    approval_block = content.index('"approval": {', evidence_builder)
    pass_marker = content.index('"verdict": "PASS"', evidence_builder)
    assert evidence_builder < pass_marker < approval_block

