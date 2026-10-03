from __future__ import annotations

import json
import subprocess
from pathlib import Path

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
        "metadata_ready_fraction",
        "max_same_row_streak_seconds",
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
