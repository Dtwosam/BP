from __future__ import annotations

import runpy
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "scripts" / "report_v4_dedupe_index_health.py"
HELPER = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase14_v4_dedupe_index_health_cloudshell.sh"
)


def test_v4_dedupe_index_health_helper_has_valid_bash() -> None:
    subprocess.run(["bash", "-n", str(HELPER)], check=True)


def test_v4_dedupe_index_health_report_is_strictly_read_only() -> None:
    source = REPORT.read_text(encoding="utf-8")
    for marker in (
        "default_transaction_read_only=on",
        "statement_timeout=5000",
        'isolation_level="AUTOCOMMIT"',
        "pg_relation_size",
        "pg_indexes_size",
        "pg_stat_user_tables",
        "pg_stat_user_indexes",
        "pg_statio_user_indexes",
        "DATABASE_WRITES_PERFORMED=false",
        "SERVICE_MUTATION_PERFORMED=false",
        "ORDER_SUBMISSION_PERFORMED=false",
    ):
        assert marker in source

    for forbidden in (
        "REINDEX",
        "VACUUM",
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


def test_v4_dedupe_index_health_report_binds_exact_child_primary_keys() -> None:
    source = REPORT.read_text(encoding="utf-8")
    for marker in (
        "EXPECTED_CHILD_COUNT = 16",
        'f"raw_event_dedupe_h{remainder:02d}"',
        'f"{name}_pkey"',
        "index_meta.indisprimary",
        "index_meta.indisunique",
        "index_meta.indisvalid",
        "index_meta.indisready",
        "all_primary_indexes_healthy",
    ):
        assert marker in source


def test_v4_dedupe_index_health_report_exposes_reindex_evidence_signal() -> None:
    source = REPORT.read_text(encoding="utf-8")
    for marker in (
        "MIN_REINDEX_SIGNAL_TOTAL_PKEY_BYTES = 5 * 1024**3",
        "MIN_REINDEX_SIGNAL_BYTES_PER_LIVE_TUPLE = 250.0",
        "primary_key_bytes_per_live_tuple",
        "primary_key_fraction_of_all_index_bytes",
        "reindex_evidence_threshold_met",
        "estimated_live_tuples",
        "estimated_dead_tuples",
        "dead_to_live_ratio",
    ):
        assert marker in source


def test_v4_dedupe_index_health_cache_ratio_helper() -> None:
    namespace = runpy.run_path(str(REPORT))
    cache_hit_ratio = namespace["_cache_hit_ratio"]

    assert cache_hit_ratio(0, 0) is None
    assert cache_hit_ratio(9, 1) == 0.9
    assert cache_hit_ratio(3, 1) == 0.75


def test_v4_dedupe_index_health_helper_requires_accepted_runtime() -> None:
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
        "RECORDER_QUEUE_MAXSIZE",
        "RECORDER_BATCH_SIZE",
        "RECORDER_WRITER_WORKERS",
        "RECORDER_FLUSH_INTERVAL_SECONDS",
        "expected recorder queue maxsize 50000",
        "expected recorder batch size 500",
        "expected 4 recorder writer workers",
        "expected recorder flush interval 0.25",
    ):
        assert marker in source


def test_v4_dedupe_index_health_helper_is_read_only_on_production() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for marker in (
        "REPORT_READ_ONLY=true",
        "REPORT_PURPOSE=dedupe_primary_key_space_and_health",
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
        "REINDEX",
        "VACUUM",
        "CREATE INDEX",
        "DROP INDEX",
        "pg_terminate_backend",
    ):
        assert forbidden not in source


def test_v4_dedupe_index_health_helper_reports_machine_memory_envelope() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for marker in (
        "MACHINE_TYPE=",
        "MACHINE_MEMORY_MB=",
        "HOST_MEM_TOTAL_BYTES=",
        "HOST_MEM_AVAILABLE_BYTES=",
        "HOST_SWAP_TOTAL_BYTES=",
        "POSTGRES_CONTAINER_MEMORY_LIMIT_BYTES=",
        "POSTGRES_CGROUP_MEMORY_MAX=",
        "gcloud compute machine-types describe",
        "docker inspect --format '{{.HostConfig.Memory}}'",
    ):
        assert marker in source
