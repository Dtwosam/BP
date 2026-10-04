from __future__ import annotations

import runpy
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "scripts" / "report_v4_compact_dedupe_readiness.py"
HELPER = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase14_v4_compact_dedupe_readiness_cloudshell.sh"
)


def test_compact_dedupe_readiness_helper_has_valid_bash() -> None:
    subprocess.run(["bash", "-n", str(HELPER)], check=True)


def test_compact_dedupe_readiness_report_is_strictly_read_only() -> None:
    source = REPORT.read_text(encoding="utf-8")
    for marker in (
        "default_transaction_read_only=on",
        "statement_timeout=120000",
        'isolation_level="AUTOCOMMIT"',
        "pg_stat_activity",
        "backend_type = 'client backend'",
        "pg_prepared_xacts",
        "shutil.disk_usage",
        "DATABASE_WRITES_PERFORMED=false",
        "SERVICE_MUTATION_PERFORMED=false",
        "ORDER_SUBMISSION_PERFORMED=false",
        "RAW_DEDUPE_KEYS_EMITTED=false",
    ):
        assert marker in source

    for forbidden in (
        "CREATE INDEX ",
        "DROP INDEX ",
        "ALTER TABLE ",
        "REINDEX ",
        "VACUUM ",
        "DELETE FROM ",
        "UPDATE raw_event_dedupe",
        "INSERT INTO raw_event_dedupe",
        "pg_terminate_backend",
    ):
        assert forbidden not in source


def test_compact_dedupe_readiness_binds_exact_digest_contract() -> None:
    namespace = runpy.run_path(str(REPORT))

    assert namespace["EXPECTED_CHILD_COUNT"] == 16
    assert namespace["CANONICAL_DEDUPE_KEY_REGEX"] == r"^sha256:[0-9a-f]{64}$"
    assert namespace["_expected_tables"]() == [
        f"raw_event_dedupe_h{value:02d}"
        for value in range(16)
    ]
    assert namespace["_expected_primary_indexes"]() == [
        f"raw_event_dedupe_h{value:02d}_pkey"
        for value in range(16)
    ]
    assert namespace["_expected_compact_indexes"]() == [
        f"raw_event_dedupe_h{value:02d}_digest_uidx"
        for value in range(16)
    ]

    source = REPORT.read_text(encoding="utf-8")
    assert "decode(substring(dedupe_key FROM 8), 'hex')" in source
    assert '"HASH (dedupe_key)"' in source
    assert '"PRIMARY KEY (dedupe_key)"' in source


def test_compact_dedupe_readiness_checks_every_child_for_noncanonical_keys() -> None:
    source = REPORT.read_text(encoding="utf-8")
    for marker in (
        "def _canonical_key_checks(",
        "for table_name in _expected_tables():",
        "WHERE dedupe_key !~ :canonical_regex",
        '"has_noncanonical_key": has_noncanonical',
        '"canonical_keys_only": canonical_keys_only',
        '"raw_dedupe_keys_emitted": False',
    ):
        assert marker in source

    assert "SELECT dedupe_key" not in source


def test_compact_dedupe_readiness_fails_closed_on_partial_prior_attempt() -> None:
    source = REPORT.read_text(encoding="utf-8")
    for marker in (
        "existing_compact_indexes",
        "no_compact_indexes_present = len(existing_compact_indexes) == 0",
        '"no_compact_indexes_present": no_compact_indexes_present',
        "invalid_or_not_ready_dedupe_indexes",
        "no_invalid_indexes",
    ):
        assert marker in source


def test_compact_dedupe_readiness_has_conservative_headroom_gate() -> None:
    namespace = runpy.run_path(str(REPORT))
    assert namespace["MIN_TRANSIENT_FREE_BYTES"] == 2 * 1024**3
    assert namespace["TRANSIENT_TOTAL_PKEY_MULTIPLIER"] == 2

    source = REPORT.read_text(encoding="utf-8")
    for marker in (
        "storage_warning_free_gib",
        "free_above_warning_reserve_bytes",
        "total_primary_key_bytes * TRANSIENT_TOTAL_PKEY_MULTIPLIER",
        "transient_required_bytes",
        "transient_headroom_ok",
    ):
        assert marker in source


def test_compact_dedupe_readiness_checks_transaction_blockers() -> None:
    source = REPORT.read_text(encoding="utf-8")
    for marker in (
        "MIN_SERVER_VERSION_NUM = 120000",
        "LONG_TRANSACTION_SECONDS = 60.0",
        "supports_concurrent_index",
        "long_transactions",
        "prepared_transactions",
        "no_long_transactions",
        "no_prepared_transactions",
        "compact_dedupe_readiness_pass",
    ):
        assert marker in source


def test_compact_dedupe_readiness_helper_requires_accepted_runtime() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for marker in (
        "52b4355d6f077373b873f7a6f42bc37a20ddbc7b",
        "bp-postgres.service",
        "bp-recorder.service",
        "bp-v3-frozen-predictor.service",
        "bp-v3-paper-execution.service",
        "bp-dashboard-api.service",
        "bp-dashboard-web.service",
        "bp-paper-execution.service",
        "bp-live-predictor.service",
        "bp-prospective-outcomes.service",
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


def test_compact_dedupe_readiness_helper_declares_writer_cutover_boundary() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for marker in (
        "DEPLOYED_WRITER_COMPACT_COMPATIBLE=false",
        "MIGRATION_REQUIRES_RUNTIME_WRITER_CUTOVER=true",
        "REPORT_PURPOSE=compact_dedupe_digest_index_readiness",
        "REPORT_READ_ONLY=true",
        "PRODUCTION_MUTATION=false",
    ):
        assert marker in source


def test_compact_dedupe_readiness_helper_is_read_only() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for forbidden in (
        "systemctl stop",
        "systemctl start",
        "systemctl restart",
        'git -C "$REPO" checkout',
        "CREATE INDEX",
        "DROP INDEX",
        "ALTER TABLE",
        "REINDEX ",
        "VACUUM ",
        "pg_terminate_backend",
    ):
        assert forbidden not in source

    assert "timeout --signal=TERM --kill-after=5s 900s" in source
