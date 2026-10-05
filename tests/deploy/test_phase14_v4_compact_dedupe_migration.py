from __future__ import annotations

import runpy
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "run_v4_compact_dedupe_migration.py"
HELPER = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase14_v4_compact_dedupe_migration_cloudshell.sh"
)


def test_compact_dedupe_migration_helper_has_valid_bash() -> None:
    subprocess.run(["bash", "-n", str(HELPER)], check=True)


def test_compact_dedupe_migration_runner_compiles() -> None:
    subprocess.run(["python", "-m", "py_compile", str(RUNNER)], check=True)


def test_compact_dedupe_migration_binds_exact_index_set() -> None:
    namespace = runpy.run_path(str(RUNNER))
    assert namespace["_expected_tables"]() == tuple(
        f"raw_event_dedupe_h{value:02d}" for value in range(16)
    )
    assert namespace["_expected_compact_indexes"]() == tuple(
        f"raw_event_dedupe_h{value:02d}_digest_uidx"
        for value in range(16)
    )


def test_compact_dedupe_migration_builds_indexes_sequentially_and_concurrently() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    for marker in (
        "CREATE UNIQUE INDEX CONCURRENTLY",
        "decode(substring(dedupe_key FROM 8), 'hex')",
        "INDEX_STATEMENT_TIMEOUT_SECONDS = 1200",
        "DDL_LOCK_TIMEOUT_SECONDS = 5",
        "for table_name, index_name in zip(",
        "strict=True",
        '"attempted_compact_indexes"',
        '"created_compact_indexes"',
    ):
        assert marker in source

    for forbidden in (
        "ThreadPoolExecutor",
        "asyncio.gather",
        "multiprocessing",
        "pg_terminate_backend",
    ):
        assert forbidden not in source


def test_compact_dedupe_migration_has_two_clean_windows_before_pk_drop() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    for marker in (
        "CLEAN_WINDOW_SAMPLES = 80",
        "CLEAN_WINDOW_INTERVAL_SECONDS = 0.5",
        'evidence_dir / "pre-clean-window.json"',
        'evidence_dir / "mutation-clean-window.json"',
        "_require_clean_window(pre_window)",
        "_require_clean_window(mutation_window)",
    ):
        assert marker in source

    pre_window = source.index('evidence_dir / "pre-clean-window.json"')
    build_index = source.index("_create_compact_index(", pre_window)
    mutation_window = source.index(
        'evidence_dir / "mutation-clean-window.json"',
        build_index,
    )
    stop_recorder = source.index("_stop_recorder()", mutation_window)
    drop_constraint = source.index(
        "_drop_parent_primary_constraint(settings)",
        stop_recorder,
    )
    assert pre_window < build_index < mutation_window < stop_recorder < drop_constraint


def test_compact_dedupe_migration_marks_irreversible_boundary_only_after_drop() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    drop_at = source.index("_drop_parent_primary_constraint(settings)")
    boundary_at = source.index(
        'summary["irreversible_boundary_crossed"] = True',
        drop_at,
    )
    post_at = source.index('evidence_dir / "after-drop-state.json"', boundary_at)
    assert drop_at < boundary_at < post_at

    before = source[:drop_at]
    assert 'summary["irreversible_boundary_crossed"] = True' not in before


def test_compact_dedupe_migration_pre_boundary_failure_is_reversible() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    for marker in (
        'if bool(summary["irreversible_boundary_crossed"]):',
        "post-boundary fail-closed: recorder stopped",
        'list(summary["attempted_compact_indexes"])',
        "_drop_compact_index(cleanup_engine, index_name)",
        '"recorder_restore"',
        "for timer in reversed(stopped_timers):",
    ):
        assert marker in source


def test_compact_dedupe_migration_post_boundary_failure_never_restores_old_writer() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    post_failure_start = source.index(
        'if bool(summary["irreversible_boundary_crossed"]):'
    )
    post_failure_end = source.index(
        "else:",
        post_failure_start,
    )
    block = source[post_failure_start:post_failure_end]
    assert '"stop"' in block
    for forbidden in (
        "git checkout",
        "phase14_prospective_runtime_install",
        "52b4355d6f077373b873f7a6f42bc37a20ddbc7b",
        "f160e8823cd2232adbd25dfc94de91bcec98219a",
    ):
        assert forbidden not in block


def test_compact_dedupe_migration_requires_steady_runtime_contract() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    for marker in (
        "ON CONFLICT DO NOTHING",
        "ON CONFLICT (dedupe_key) DO NOTHING",
        "_compact_dedupe_indexes_healthy",
        "partitioned dedupe uniqueness contract is missing or unhealthy",
        "deployed recorder is not target-free",
        "deployed recorder still binds the old conflict target",
    ):
        assert marker in source


def test_compact_dedupe_migration_requires_research_zero_money() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    for marker in (
        '"MODE": "research"',
        '"LIVE_TRADING_ENABLED": "false"',
        '"MAX_TRADE_SIZE_USD": "0"',
        '"MAX_DAILY_LOSS_USD": "0"',
        "automatic_promotion must remain false",
        "recorder_queue_maxsize",
        "recorder_batch_size",
        "recorder_writer_workers",
        "recorder_flush_interval_seconds",
    ):
        assert marker in source


def test_compact_dedupe_migration_helper_binds_exact_evidence_and_approval() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for marker in (
        "PHASE14_V4_COMPACT_DEDUPE_MIGRATION_READINESS_EVIDENCE",
        "PHASE14_V4_COMPACT_DEDUPE_MIGRATION_READINESS_SHA256",
        "PHASE14_V4_COMPACT_DEDUPE_MIGRATION_BLOCKER_EVIDENCE",
        "PHASE14_V4_COMPACT_DEDUPE_MIGRATION_BLOCKER_SHA256",
        "readiness_evidence_sha256_mismatch",
        "blocker_evidence_sha256_mismatch",
        "LOCAL_EVIDENCE_VALIDATION=PASS",
        "canonical_keys_only",
        "current_primary_contract_ok",
        "no_compact_indexes_present",
        "persistent_long_transaction_count",
        "persistent_writer_like_count",
        "writer_quiesce_likely_required_for_bounded_reindex",
        "I_APPROVE_PHASE14_V4_COMPACT_DEDUPE_MIGRATION:",
    ):
        assert marker in source


def test_compact_dedupe_migration_helper_validates_before_cloud_contact() -> None:
    source = HELPER.read_text(encoding="utf-8")
    evidence_validation = source.index("LOCAL_EVIDENCE_VALIDATION=PASS")
    approval = source.index("production_approval_mismatch", evidence_validation)
    gcloud = source.index("gcloud auth list", approval)
    assert evidence_validation < approval < gcloud


def test_compact_dedupe_migration_helper_is_macos_hash_compatible() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for marker in (
        "command -v sha256sum",
        "command -v shasum",
        "shasum -a 256",
        "sha256_tool_missing",
    ):
        assert marker in source


def test_compact_dedupe_migration_helper_streams_without_checkout_mutation() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for marker in (
        "run_v4_compact_dedupe_migration.py",
        "report_v4_dedupe_reindex_blockers.py",
        "DATABASE_SCHEMA_MUTATION=true",
        "IRREVERSIBLE_BOUNDARY=DROP_RAW_EVENT_DEDUPE_PRIMARY_CONSTRAINT",
        "REMOTE_SCRIPT_PATH",
        "PIPESTATUS",
        "remote_terminal_marker_missing",
    ):
        assert marker in source

    for forbidden in (
        'git -C "$REPO" checkout',
        "pg_terminate_backend",
        "VACUUM ",
    ):
        assert forbidden not in source
