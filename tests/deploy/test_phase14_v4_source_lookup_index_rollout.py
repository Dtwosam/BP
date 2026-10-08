from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase14_v4_source_lookup_index_rollout_cloudshell.sh"
)
MIGRATION = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase14_v4_source_lookup_index_migration.py"
)


def test_v4_source_lookup_rollout_helper_has_valid_bash() -> None:
    subprocess.run(["bash", "-n", str(HELPER)], check=True)


def test_v4_source_lookup_rollout_preflight_is_non_contacting() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for marker in (
        "PHASE14_V4_SOURCE_LOOKUP_INDEX_PREFLIGHT_ONLY",
        "PHASE14_V4_SOURCE_LOOKUP_INDEX_ROLLOUT_PREFLIGHT=PASS",
        "PRODUCTION_HOST_CONTACTED=false",
        "PRODUCTION_MUTATION_PERFORMED=false",
        "DATABASE_DDL_PERFORMED=false",
        "I_APPROVE_PHASE14_V4_SOURCE_LOOKUP_INDEX_ROLLOUT",
        "2bac3b4c20ae5d1fb6fb8caa80edaf1928674706",
    ):
        assert marker in source
    assert source.index("ROLLOUT_PREFLIGHT=PASS") < source.index(
        "command -v gcloud"
    )


def test_v4_source_lookup_rollout_preserves_recorder_and_shadow() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for marker in (
        "recorder_pid_changed",
        "recorder_restarted",
        "shadow_pid_changed",
        "shadow_restarted",
        'systemctl stop "$MAINTENANCE_TIMER"',
        'systemctl start "$MAINTENANCE_TIMER"',
        "validate_shadow_contract",
        "require_recorder_batch100",
        "fast_live_source_active",
    ):
        assert marker in source
    for forbidden in (
        'systemctl stop "$RECORDER_UNIT"',
        'systemctl restart "$RECORDER_UNIT"',
        'systemctl stop "$EXPECTED_SHADOW_UNIT"',
        'systemctl restart "$EXPECTED_SHADOW_UNIT"',
        "LIVE_TRADING_ENABLED=true",
    ):
        assert forbidden not in source


def test_v4_source_lookup_rollout_scope_is_exact() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for path in (
        "scripts/deploy/phase14_v4_source_lookup_index_migration.py",
        "src/bp_engine/storage/partitioned_raw.py",
        "tests/storage/test_partitioned_raw_postgres.py",
    ):
        assert path in source
    assert "candidate_scope_mismatch" in source
    assert "candidate_blob_not_exact_main" in source


def test_v4_source_lookup_migration_is_concurrent_and_rollbackable() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for marker in (
        "CREATE INDEX CONCURRENTLY",
        "DROP INDEX CONCURRENTLY IF EXISTS",
        'isolation_level="AUTOCOMMIT"',
        "(source, stream, instrument, received_at DESC, id DESC)",
        "WHERE source_timestamp IS NOT NULL",
        "statement_timeout = '15min'",
        "statement_timeout = '2s'",
        "BENCHMARK_REQUESTED_AT",
        "raw_market_events_20261007_20",
        "recorder_batch_size",
        "--rollback-evidence",
    ):
        assert marker in source


def test_v4_source_lookup_migration_targets_live_window() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for marker in (
        "hour - timedelta(hours=1)",
        "hour + timedelta(hours=1)",
        "hour + timedelta(hours=2)",
        "_attached_partition",
        "benchmark planner did not choose",
        "benchmark query did not finish inside 2s",
    ):
        assert marker in source


def test_v4_source_lookup_rollout_keeps_temp_root_owned_until_write() -> None:
    source = HELPER.read_text(encoding="utf-8")
    mktemp_pos = source.index(
        "mktemp /var/tmp/bp-v4-source-lookup-migration.XXXXXX.json"
    )
    redirect_pos = source.index('> "$MIGRATION_TMP"')
    chown_pos = source.index('chown bp:bp "$MIGRATION_TMP"')
    assert mktemp_pos >= 0
    assert redirect_pos > mktemp_pos
    assert chown_pos > redirect_pos
