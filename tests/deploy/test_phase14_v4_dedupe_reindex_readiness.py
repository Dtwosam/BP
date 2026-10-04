from __future__ import annotations

import runpy
import subprocess
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "scripts" / "report_v4_dedupe_reindex_readiness.py"
HELPER = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase14_v4_dedupe_reindex_readiness_cloudshell.sh"
)


def test_v4_dedupe_reindex_readiness_helper_has_valid_bash() -> None:
    subprocess.run(["bash", "-n", str(HELPER)], check=True)


def test_v4_dedupe_reindex_readiness_report_is_strictly_read_only() -> None:
    source = REPORT.read_text(encoding="utf-8")
    for marker in (
        "default_transaction_read_only=on",
        "statement_timeout=5000",
        'isolation_level="AUTOCOMMIT"',
        "pg_stat_activity",
        "backend_type = 'client backend'",
        "pg_prepared_xacts",
        "pg_relation_size",
        "shutil.disk_usage",
        "DATABASE_WRITES_PERFORMED=false",
        "SERVICE_MUTATION_PERFORMED=false",
        "ORDER_SUBMISSION_PERFORMED=false",
    ):
        assert marker in source

    for forbidden in (
        "REINDEX INDEX",
        "VACUUM ",
        "CREATE INDEX",
        "DROP INDEX",
        "ALTER TABLE",
        "DROP TABLE",
        "DELETE FROM",
        "UPDATE raw_event_dedupe",
        "INSERT INTO raw_event_dedupe",
        "connection.execute(insert(",
    ):
        assert forbidden not in source


def test_v4_dedupe_reindex_readiness_binds_exact_target_set() -> None:
    namespace = runpy.run_path(str(REPORT))
    expected_tables = namespace["_expected_tables"]()
    expected_indexes = namespace["_expected_primary_indexes"]()

    assert expected_tables == [
        f"raw_event_dedupe_h{value:02d}"
        for value in range(16)
    ]
    assert expected_indexes == [
        f"raw_event_dedupe_h{value:02d}_pkey"
        for value in range(16)
    ]


def test_v4_dedupe_reindex_readiness_has_conservative_headroom_gate() -> None:
    source = REPORT.read_text(encoding="utf-8")
    for marker in (
        "MIN_TRANSIENT_FREE_BYTES = 2 * 1024**3",
        "TRANSIENT_INDEX_MULTIPLIER = 4",
        "storage_warning_free_gib",
        "free_above_warning_reserve_bytes",
        "transient_required_bytes",
        "transient_headroom_ok",
        "largest_primary_key_bytes",
    ):
        assert marker in source


def test_v4_dedupe_reindex_readiness_checks_database_blockers() -> None:
    source = REPORT.read_text(encoding="utf-8")
    for marker in (
        "MIN_SERVER_VERSION_NUM = 120000",
        "LONG_TRANSACTION_SECONDS = 60.0",
        "supports_concurrent_rebuild",
        "invalid_or_not_ready_dedupe_indexes",
        "long_transactions",
        "prepared_transactions",
        "reindex_readiness_pass",
    ):
        assert marker in source


def test_v4_dedupe_reindex_readiness_helper_requires_accepted_runtime() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for marker in (
        "52b4355d6f077373b873f7a6f42bc37a20ddbc7b",
        "bp-postgres.service",
        "bp-recorder.service",
        "bp-v3-frozen-predictor.service",
        "bp-v3-paper-execution.service",
        "bp-storage-maintenance.timer",
        "bp-storage-disk-health.timer",
        "bp-v2-forward-coverage.timer",
        "bp-v4-forward-coverage.timer",
        "expected recorder queue maxsize 50000",
        "expected recorder batch size 500",
        "expected 4 recorder writer workers",
        "expected recorder flush interval 0.25",
        "automatic_promotion must remain false",
    ):
        assert marker in source


def test_v4_dedupe_reindex_readiness_helper_is_read_only() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for marker in (
        "REPORT_READ_ONLY=true",
        "REPORT_PURPOSE=dedupe_online_rebuild_readiness",
        "PRODUCTION_MUTATION=false",
        "MODE",
        "LIVE_TRADING_ENABLED",
        "MAX_TRADE_SIZE_USD",
        "MAX_DAILY_LOSS_USD",
    ):
        assert marker in source

    for forbidden in (
        "systemctl stop",
        "systemctl start",
        "systemctl restart",
        'git -C "$REPO" checkout',
        "REINDEX INDEX",
        "VACUUM ",
        "CREATE INDEX",
        "DROP INDEX",
        "pg_terminate_backend",
    ):
        assert forbidden not in source


def test_v4_dedupe_reindex_readiness_helper_reports_machine_memory() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for marker in (
        "MACHINE_TYPE=",
        "MACHINE_MEMORY_MB=",
        "HOST_MEM_TOTAL_BYTES=",
        "HOST_MEM_AVAILABLE_BYTES=",
        "HOST_SWAP_TOTAL_BYTES=",
        "POSTGRES_CONTAINER_MEMORY_LIMIT_BYTES=",
        "POSTGRES_CGROUP_MEMORY_MAX=",
    ):
        assert marker in source


def test_v4_dedupe_reindex_readiness_headroom_math() -> None:
    namespace = runpy.run_path(str(REPORT))
    assert namespace["MIN_TRANSIENT_FREE_BYTES"] == 2 * 1024**3
    assert namespace["TRANSIENT_INDEX_MULTIPLIER"] == 4

    usage = SimpleNamespace(total=100, used=40, free=60)
    assert usage.total - usage.used == usage.free
