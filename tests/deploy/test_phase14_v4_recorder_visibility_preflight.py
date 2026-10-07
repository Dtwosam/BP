from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, insert

from bp_engine.storage.schema import metadata, raw_market_events
from scripts.report_v4_recorder_visibility import _latest_row

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "scripts" / "report_v4_recorder_visibility.py"
HELPER = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase14_v4_recorder_visibility_preflight_cloudshell.sh"
)


def test_v4_visibility_preflight_helper_has_valid_bash() -> None:
    subprocess.run(["bash", "-n", str(HELPER)], check=True)


def test_v4_visibility_report_is_read_only_and_bounded() -> None:
    source = REPORT.read_text(encoding="utf-8")
    for marker in (
        "default_transaction_read_only=on",
        "statement_timeout=2000",
        'isolation_level="AUTOCOMMIT"',
        "LOOKBACK_SECONDS = 10.0",
        "DEFAULT_SAMPLES = 80",
        "timestamp_window_ready_fraction",
        "max_same_row_streak_seconds",
        "--ticker-only",
        '"event_contract": "ticker_only" if ticker_only else "ticker_or_trade"',
        "pg_stat_activity",
        "pg_blocking_pids",
        "DATABASE_WRITES_PERFORMED=false",
        "SERVICE_MUTATION_PERFORMED=false",
        "ORDER_SUBMISSION_PERFORMED=false",
    ):
        assert marker in source
    for forbidden in (
        "INSERT INTO",
        "UPDATE raw_market_events",
        "DELETE FROM raw_market_events",
        "DROP TABLE",
        "ALTER TABLE",
        "CREATE TABLE",
        "CREATE INDEX",
    ):
        assert forbidden not in source


def test_v4_visibility_ticker_only_mode_ignores_newer_trade_row() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    observed = datetime(2026, 10, 7, 11, 30, tzinfo=UTC)
    with engine.begin() as connection:
        connection.execute(
            insert(raw_market_events),
            [
                {
                    "source": "bybit",
                    "stream": "spot",
                    "instrument": "BTCUSDT",
                    "event_type": "ticker",
                    "source_timestamp": observed - timedelta(seconds=0.4),
                    "received_at": observed - timedelta(seconds=0.3),
                    "sequence": "ticker",
                    "market_id": None,
                    "asset_id": None,
                    "payload": {"data": {"lastPrice": "1"}},
                    "dedupe_key": "ticker-only-test-ticker",
                },
                {
                    "source": "bybit",
                    "stream": "spot",
                    "instrument": "BTCUSDT",
                    "event_type": "trade",
                    "source_timestamp": observed - timedelta(seconds=0.2),
                    "received_at": observed - timedelta(seconds=0.1),
                    "sequence": "trade",
                    "market_id": None,
                    "asset_id": None,
                    "payload": {"data": [{"p": "1"}]},
                    "dedupe_key": "ticker-only-test-trade",
                },
            ],
        )
        mixed = _latest_row(
            connection,
            venue="bybit_spot",
            observed_at=observed,
        )
        ticker = _latest_row(
            connection,
            venue="bybit_spot",
            observed_at=observed,
            ticker_only=True,
        )

    engine.dispose()
    assert mixed is not None and mixed["event_type"] == "trade"
    assert ticker is not None and ticker["event_type"] == "ticker"


def test_v4_visibility_preflight_is_read_only_and_head_bound() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for marker in (
        "52b4355d6f077373b873f7a6f42bc37a20ddbc7b",
        "git fetch origin main",
        "git pull --ff-only origin main",
        "unexpected_deployed_head",
        "RECORDER_WRITER_WORKERS",
        "RECORDER_FLUSH_INTERVAL_SECONDS",
        "bp-recorder.service",
        "bp-v3-frozen-predictor.service",
        "bp-v3-paper-execution.service",
        "bp-storage-maintenance.timer",
        "bp-v4-forward-coverage.timer",
        "timeout --signal=TERM --kill-after=5s 60s",
        "PRODUCTION_MUTATION=false",
    ):
        assert marker in source
    for forbidden in (
        "systemctl stop",
        "systemctl start",
        "systemctl restart",
        'git -C "$REPO" checkout',
        "pg_terminate_backend",
    ):
        assert forbidden not in source


def test_project_state_matches_visibility_preflight_expected_head() -> None:
    state = json.loads((ROOT / "PROJECT_STATE.json").read_text(encoding="utf-8"))
    storage = state["phase_14_storage_reliability_followup"]
    assert (
        storage["recorder_v3_current_runtime_deployed_head"]
        == "52b4355d6f077373b873f7a6f42bc37a20ddbc7b"
    )
    assert storage["recorder_v3_current_runtime_recorder_active"] is True


def test_v4_visibility_preflight_supports_explicit_recovery_handoff_mode() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for marker in (
        "PHASE14_V4_VISIBILITY_ALLOW_MAINTENANCE_HANDOFF",
        "maintenance_handoff_flag_invalid",
        "maintenance_timer_active_during_handoff",
        "MAINTENANCE_HANDOFF_MODE",
    ):
        assert marker in source
    assert 'systemctl is-enabled --quiet bp-storage-maintenance.timer' in source
    assert 'systemctl is-active --quiet bp-storage-maintenance.timer' in source
