from __future__ import annotations

import runpy
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "scripts" / "report_v4_dedupe_reindex_blockers.py"
HELPER = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase14_v4_dedupe_reindex_blockers_cloudshell.sh"
)


def test_v4_dedupe_reindex_blockers_helper_has_valid_bash() -> None:
    subprocess.run(["bash", "-n", str(HELPER)], check=True)


def test_v4_dedupe_reindex_blockers_report_is_strictly_read_only() -> None:
    source = REPORT.read_text(encoding="utf-8")
    for marker in (
        "default_transaction_read_only=on",
        "statement_timeout=5000",
        'isolation_level="AUTOCOMMIT"',
        "pg_stat_activity",
        "activity.backend_type = 'client backend'",
        "pg_locks",
        "RAW_QUERY_TEXT_EMITTED=false",
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
        "INSERT INTO raw_event_dedupe (",
        "pg_terminate_backend",
    ):
        assert forbidden not in source


def test_v4_dedupe_reindex_blockers_classifies_recorder_phases() -> None:
    namespace = runpy.run_path(str(REPORT))
    query_class = namespace["_query_class"]

    assert query_class(
        "INSERT INTO raw_event_dedupe (dedupe_key) VALUES ($1)"
    ) == "dedupe_insert"
    assert query_class(
        "INSERT INTO raw_market_events (id) VALUES ($1)"
    ) == "raw_insert"
    assert query_class(
        "INSERT INTO market_state_1s (id) VALUES ($1)"
    ) == "state_upsert"
    assert query_class(
        "DELETE FROM raw_event_dedupe_h00 WHERE received_at < $1"
    ) == "dedupe_cleanup"
    assert query_class(
        "DELETE FROM raw_market_events WHERE received_at < $1"
    ) == "raw_cleanup"
    assert query_class(
        "SELECT * FROM raw_event_dedupe_h00"
    ) == "select"
    assert query_class("COMMIT") == "other"


def test_v4_dedupe_reindex_blockers_fingerprint_hides_query_text() -> None:
    namespace = runpy.run_path(str(REPORT))
    fingerprint = namespace["_query_fingerprint"]

    first = fingerprint("SELECT 1")
    second = fingerprint("SELECT 2")
    assert first is not None
    assert second is not None
    assert first != second
    assert len(first) == 64


def test_v4_dedupe_reindex_blockers_has_temporal_persistence_signal() -> None:
    source = REPORT.read_text(encoding="utf-8")
    for marker in (
        "DEFAULT_SAMPLES = 80",
        "DEFAULT_INTERVAL_SECONDS = 0.5",
        "DEFAULT_LONG_TRANSACTION_SECONDS = 60.0",
        "persistent_first_to_last_sample",
        "persistent_long_transaction_count",
        "persistent_writer_like_count",
        "writer_quiesce_likely_required_for_bounded_reindex",
        "cleared_pids",
        "appeared_pids",
    ):
        assert marker in source


def test_v4_dedupe_reindex_blockers_does_not_emit_raw_query() -> None:
    source = REPORT.read_text(encoding="utf-8")
    assert '"query": query' not in source
    assert '"query_text"' not in source
    assert '"raw_query_text_emitted": False' in source
    assert "query_fingerprint" in source
    assert "query_class" in source


def test_v4_dedupe_reindex_blockers_helper_requires_accepted_runtime() -> None:
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


def test_v4_dedupe_reindex_blockers_helper_is_read_only() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for marker in (
        "REPORT_READ_ONLY=true",
        "REPORT_PURPOSE=dedupe_long_transaction_blocker_attribution",
        "PRODUCTION_MUTATION=false",
        "MODE",
        "LIVE_TRADING_ENABLED",
        "MAX_TRADE_SIZE_USD",
        "MAX_DAILY_LOSS_USD",
        "timeout --signal=TERM --kill-after=5s 90s",
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


def test_v4_dedupe_reindex_blockers_classifies_allowlisted_relation_families() -> None:
    namespace = runpy.run_path(str(REPORT))
    relation_families = namespace["_relation_families"]
    service_signature = namespace["_service_signature"]

    query = (
        "SELECT paper_orders.paper_order_id, raw_market_events.received_at "
        "FROM paper_orders JOIN raw_market_events ON true"
    )
    assert relation_families(query) == ("paper_execution", "market_replay")
    assert service_signature(query) == "legacy_paper_execution"

    prospective = (
        "SELECT live_prediction_evaluations.prediction_id, market_labels.label_version "
        "FROM live_prediction_evaluations JOIN market_labels ON true"
    )
    assert relation_families(prospective) == (
        "live_prediction",
        "labels_features",
    )
    assert service_signature(prospective) == "prospective_outcomes_like"


def test_v4_dedupe_reindex_blockers_query_signatures_do_not_emit_sql_text() -> None:
    source = REPORT.read_text(encoding="utf-8")
    for marker in (
        '"relation_families"',
        '"service_signatures"',
        '"legacy_paper_execution"',
        '"prospective_outcomes_like"',
        '"raw_query_text_emitted": False',
    ):
        assert marker in source
    assert '"query": query' not in source
    assert '"query_text"' not in source
