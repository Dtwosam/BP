from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts import report_v4_cache_bulk_lane_evidence as evidence

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "scripts" / "report_v4_cache_bulk_lane_evidence.py"
RUNNER = ROOT / "scripts" / "run_v4_postgres_cache2g_batch100_ab.py"
HELPER = (
    ROOT / "scripts" / "deploy"
    / "phase14_v4_postgres_cache2g_batch100_ab_cloudshell.sh"
)


class _ReadOnly:
    class _Value:
        def scalar_one(self):
            return "on"

    def execute(self, _statement):
        return self._Value()


def test_sampled_bybit_advance_and_age_drift() -> None:
    t0 = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    first = {
        "status": "observed",
        "received_at": t0.isoformat(),
        "received_age_seconds": 245.0,
    }
    last = {
        "status": "observed",
        "received_at": (t0 + timedelta(seconds=9)).isoformat(),
        "received_age_seconds": 251.0,
    }
    output = evidence._event_progress(first, last, elapsed=15)
    assert output["advance_to_wall_ratio"] == 0.6
    assert output["lag_growth_seconds"] == 6.0


def test_missing_and_query_timeout_are_inconclusive() -> None:
    a = {"status": "no_recent_event"}
    b = {"status": "query_timeout_or_error"}
    output = evidence._event_progress(a, b, elapsed=30)
    assert output["status"] == "inconclusive"


def test_index_delta_and_event_samples_are_read_only(monkeypatch) -> None:
    snapshots = []
    for offset in (0.0, 1.0):
        snapshots.append({
            f"{stream}_{kind}": {
                "status": "observed",
                "row_id": index + 1,
                "received_at": (
                    datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
                    + timedelta(seconds=offset)
                ).isoformat(),
                "received_age_seconds": 1.0,
            }
            for index, (stream, kind) in enumerate(evidence.EVENT_SPECS)
        })
    monkeypatch.setattr(
        evidence, "_event_snapshot",
        lambda *_args, **_kwargs: snapshots.pop(0),
    )
    indexes = [
        {"reads": 100, "hits": 800},
        {"reads": 130, "hits": 1070},
    ]
    monkeypatch.setattr(
        evidence, "_index_snapshot",
        lambda *_args, **_kwargs: indexes.pop(0),
    )
    report = evidence.capture_report(
        _ReadOnly(), sample_seconds=30, sleep_fn=lambda _seconds: None
    )
    assert report["complete"] is True
    assert report["digest_index_delta"]["buffer_reads"] == 30
    assert report["digest_index_delta"]["buffer_hits"] == 270
    assert report["digest_index_delta"]["interval_hit_ratio"] == 0.9
    assert report["safety"]["database_writes_performed"] is False


def test_elapsed_and_index_stats_fail_closed(monkeypatch) -> None:
    monkeypatch.setattr(evidence, "_event_snapshot", lambda *_a, **_kw: {
        f"{stream}_{kind}": {"status": "no_recent_event"}
        for stream, kind in evidence.EVENT_SPECS
    })
    values = iter([
        {"reads": 1000, "hits": 3000},
        {"reads": 900, "hits": 5000},
    ])
    monkeypatch.setattr(
        evidence, "_index_snapshot",
        lambda *_a, **_kw: next(values),
    )
    with pytest.raises(RuntimeError, match="statistics reset"):
        evidence.capture_report(
            _ReadOnly(), sample_seconds=30, sleep_fn=lambda _seconds: None
        )
    with pytest.raises(ValueError):
        evidence.capture_report(_ReadOnly(), sample_seconds=1)


def test_probe_has_bounded_db_reads_and_exact_event_classes() -> None:
    source = REPORT.read_text(encoding="utf-8")
    for token in (
        "default_transaction_read_only=on",
        "statement_timeout=1500",
        'isolation_level="AUTOCOMMIT"',
        "SOURCE_LOOKBACK_MINUTES = 15",
        "INDEX_COUNT = 16",
        '"orderbook"',
        '"trade"',
        '"ticker"',
        "idx_blks_read",
        "idx_blks_hit",
        "connection.rollback()",
    ):
        assert token in source
    for forbidden in (
        "REINDEX ",
        "VACUUM ",
        "DELETE FROM raw_market_events",
        "INSERT INTO raw_market_events",
        "DROP INDEX",
        "CREATE INDEX",
        "systemctl start",
        "systemctl restart",
    ):
        assert forbidden not in source


def test_batch100_cache_runner_is_restoring_manual_review_only() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    for token in (
        "EXPECTED_BATCH_SIZE = 100",
        '"recorder_priority_batch_size": 20',
        '"recorder_priority_queue_maxsize": 5_000',
        "EXPECTED_WRITER_WORKERS = 4",
        'BASELINE_SHARED_BUFFERS = "128MB"',
        'CANDIDATE_SHARED_BUFFERS = "2GB"',
        "MIN_BASELINE_AVAILABLE_BYTES = 4 * 1024**3",
        "MIN_CANDIDATE_AVAILABLE_BYTES = 2 * 1024**3",
        'evidence_dir / "baseline-bulk-and-cache.json"',
        'evidence_dir / "candidate-bulk-and-cache.json"',
        'evidence_dir / "baseline-commit-lag.json"',
        'evidence_dir / "candidate-commit-lag.json"',
        '"automatic_candidate_promotion": False',
        '"manual_review_required": True',
        "signal.signal(signal.SIGTERM, _abort)",
        "except BaseException as exc:",
        'print("PHASE=emergency_restore"',
        'PHASE=candidate_recreate_postgres_2GB',
        'PHASE=restore_deployed_postgres_128MB',
    ):
        assert token in source
    assert "EXPECTED_BATCH_SIZE = 500" not in source
    assert "TARGET_BATCH_SIZE" not in source


def test_shell_wrapper_is_preflight_first_and_requires_approval() -> None:
    subprocess.run(["bash", "-n", str(HELPER)], check=True)
    source = HELPER.read_text(encoding="utf-8")
    for token in (
        'PREFLIGHT_ONLY="${PHASE14_V4_PG_CACHE2G_BATCH100_AB_PREFLIGHT_ONLY:-true}"',
        "EXPECTED_BATCH_SIZE=100",
        "PHASE14_V4_PG_CACHE2G_BATCH100_AB_PREFLIGHT=PASS",
        "PRODUCTION_HOST_CONTACTED=false",
        "PRODUCTION_MUTATION=false",
        "EXPECTED_APPROVAL=",
        '[[ "$APPROVAL" == "$EXPECTED_APPROVAL" ]]',
        "remote_main_changed",
        "local_helper_head_mismatch",
        "production_approval_mismatch",
        "scripts/report_v4_cache_bulk_lane_evidence.py",
    ):
        assert token in source
    assert source.index('if [[ "$PREFLIGHT_ONLY" == "true" ]]') < source.index(
        '[[ "$APPROVAL" == "$EXPECTED_APPROVAL" ]]'
    )
    assert "EXPECTED_BATCH_SIZE=500" not in source
