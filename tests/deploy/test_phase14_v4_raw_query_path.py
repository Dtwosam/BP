from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "scripts" / "report_v4_raw_query_path.py"
HELPER = ROOT / "scripts" / "deploy" / "phase14_v4_raw_query_path_cloudshell.sh"


def test_v4_raw_query_path_helper_is_shell_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(HELPER)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_v4_raw_query_path_is_read_only_metadata_and_explain() -> None:
    source = REPORT.read_text(encoding="utf-8")
    for marker in (
        "default_transaction_read_only=on",
        "SHOW default_transaction_read_only",
        "pg_index",
        "pg_stat_activity",
        "pg_blocking_pids",
        "pg_inherits",
        "pg_total_relation_size",
        "EXPLAIN (FORMAT JSON)",
        "statement_timeout=3000",
        "_safe_section",
        "v4_raw_query_path_v3",
        "database_writes_performed",
        "order_submission_performed",
    ):
        assert marker in source
    for forbidden in (
        "INSERT INTO",
        "UPDATE raw_market_events",
        "DELETE FROM raw_market_events",
        "DROP TABLE",
        "ALTER TABLE",
        "CREATE INDEX",
    ):
        assert forbidden not in source


def test_v4_raw_query_path_helper_uses_latest_evidence_and_runtime_source() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for marker in (
        "git fetch origin main",
        "git pull --ff-only origin main",
        "REPORT_READ_ONLY=true",
        "report_v4_raw_query_path.py",
        "files=(/var/lib/bp/evidence/v4-fresh-book-shadow-*.jsonl)",
        "v4-source-time-fresh-book-shadow-",
        "PYTHONPATH=",
        "timeout --signal=TERM --kill-after=5s 120s",
        "--evidence-file",
    ):
        assert marker in source


def test_v4_raw_query_path_supports_exact_slow_anchor_plan() -> None:
    report = REPORT.read_text(encoding="utf-8")
    helper = HELPER.read_text(encoding="utf-8")
    for marker in (
        "--requested-at",
        "--venue",
        "source_timestamp >= :source_lower",
        "source_timestamp <= :source_upper",
        "event_type IN ('ticker', 'trade')",
        '"exact_query": exact_query',
    ):
        assert marker in report
    for marker in (
        "--requested-at 2026-10-07T20:49:00Z",
        "--venue bybit_spot",
    ):
        assert marker in helper
