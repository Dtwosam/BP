from __future__ import annotations

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


def test_v4_commit_lag_probe_is_read_only_and_unbounded_by_recent_window() -> None:
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
        "INSERT INTO raw_market_events",
        "INSERT INTO raw_event_dedupe",
        "DATABASE_WRITES_PERFORMED=false",
        "SERVICE_MUTATION_PERFORMED=false",
        "ORDER_SUBMISSION_PERFORMED=false",
    ):
        assert marker in source
    assert "LOOKBACK_SECONDS" not in source
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
