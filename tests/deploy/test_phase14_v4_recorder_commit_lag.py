from __future__ import annotations

import runpy
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "scripts" / "report_v4_recorder_commit_lag.py"
HELPER = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase14_v4_recorder_commit_lag_cloudshell.sh"
)


def test_v4_commit_lag_helper_has_valid_bash() -> None:
    subprocess.run(["bash", "-n", str(HELPER)], check=True)


def test_v4_commit_lag_probe_is_read_only_and_partition_pruned() -> None:
    source = REPORT.read_text(encoding="utf-8")
    for marker in (
        "default_transaction_read_only=on",
        "statement_timeout=2000",
        'isolation_level="AUTOCOMMIT"',
        "commit_advancement_ratio",
        "committed_received_at_advance_seconds",
        "commit_lag_seconds",
        "xact_age_seconds",
        "query_age_seconds",
        "active_writer_count",
        "writer_phase_state",
        "pg_stat_database",
        "pg_stat_wal",
        "wal_bytes_per_xact_commit",
        "pg_total_relation_size",
        "pg_indexes_size",
        "INSERT INTO raw_market_events",
        "INSERT INTO raw_event_dedupe",
        "DATABASE_WRITES_PERFORMED=false",
        "SERVICE_MUTATION_PERFORMED=false",
        "ORDER_SUBMISSION_PERFORMED=false",
    ):
        assert marker in source
    for marker in (
        "DEFAULT_HORIZON_HOURS = 6.0",
        "--horizon-hours",
        "received_at",
        "timedelta(hours=horizon_hours)",
        "no_row_within_horizon_count",
        '"horizon_hours": horizon_hours',
    ):
        assert marker in source
    for forbidden in (
        "connection.execute(insert(",
        "UPDATE raw_market_events",
        "DELETE FROM raw_market_events",
        "DROP TABLE",
        "ALTER TABLE",
        "CREATE TABLE",
        "CREATE INDEX",
    ):
        assert forbidden not in source


def test_v4_commit_lag_probe_matches_v4_source_contract() -> None:
    source = REPORT.read_text(encoding="utf-8")
    for marker in (
        '"coinbase": ("coinbase", "spot", "BTC-USD")',
        '"bybit_spot": ("bybit", "spot", "BTCUSDT")',
        '"bybit_linear": ("bybit", "linear", "BTCUSDT")',
        'raw_market_events.c.source_timestamp.is_not(None)',
        'raw_market_events.c.event_type.like("ticker_%")',
        'raw_market_events.c.event_type.like("market_trades_%")',
        'raw_market_events.c.event_type.in_(("ticker", "trade"))',
    ):
        assert marker in source


def test_v4_commit_lag_helper_requires_rolled_back_healthy_runtime() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for marker in (
        "52b4355d6f077373b873f7a6f42bc37a20ddbc7b",
        "bp-recorder.service",
        "bp-v3-frozen-predictor.service",
        "bp-v3-paper-execution.service",
        "bp-storage-maintenance.timer",
        "bp-storage-disk-health.timer",
        "bp-v2-forward-coverage.timer",
        "bp-v4-forward-coverage.timer",
        "RECORDER_WRITER_WORKERS",
        "RECORDER_FLUSH_INTERVAL_SECONDS",
        "timeout --signal=TERM --kill-after=5s 60s",
        "PRODUCTION_MUTATION=false",
    ):
        assert marker in source
    for forbidden in (
        "systemctl stop",
        "systemctl start",
        "systemctl restart",
        "git -C \"$REPO\" checkout",
        "pg_terminate_backend",
    ):
        assert forbidden not in source


def test_v4_commit_lag_probe_pruning_does_not_hide_severe_lag() -> None:
    source = REPORT.read_text(encoding="utf-8")
    assert 'state["no_row_within_horizon_count"] += 1' in source
    assert '"no_row_within_horizon_count": int(' in source
    assert "commit_lag_seconds" in source
    assert "commit_advancement_ratio" in source


def test_v4_commit_lag_row_change_count_handles_adjacent_pairs() -> None:
    namespace = runpy.run_path(str(REPORT))
    row_change_count = namespace["_row_change_count"]

    assert row_change_count([]) == 0
    assert row_change_count([10]) == 0
    assert row_change_count([10, 11, 11, 12]) == 2


def test_v4_commit_lag_writer_phase_classification() -> None:
    namespace = runpy.run_path(str(REPORT))
    writer_phase = namespace["_writer_phase"]

    assert writer_phase("INSERT INTO raw_market_events (id) VALUES (1)") == "raw_insert"
    assert writer_phase("INSERT INTO raw_event_dedupe (dedupe_key) VALUES ('x')") == (
        "dedupe_insert"
    )
    assert writer_phase("SELECT 1") == "other"


def test_v4_commit_lag_counter_delta_is_monotone_and_fail_soft() -> None:
    namespace = runpy.run_path(str(REPORT))
    counter_delta = namespace["_counter_delta"]

    assert counter_delta(None, {"x": 2.0}) is None
    assert counter_delta({"x": 1.0}, None) is None
    assert counter_delta({"x": 5.0, "y": 2.0}, {"x": 8.0, "y": 1.0}) == {
        "x": 3.0,
        "y": 0.0,
    }
