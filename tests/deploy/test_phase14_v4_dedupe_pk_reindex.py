from __future__ import annotations

import runpy
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "reindex_v4_dedupe_primary_indexes.py"
ROLLOUT = ROOT / "scripts" / "run_v4_dedupe_pk_reindex_rollout.py"
HELPER = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase14_v4_dedupe_pk_reindex_cloudshell.sh"
)


def test_v4_dedupe_pk_reindex_helper_has_valid_bash() -> None:
    subprocess.run(["bash", "-n", str(HELPER)], check=True)


def test_v4_dedupe_pk_reindex_runner_binds_exact_16_primary_indexes() -> None:
    namespace = runpy.run_path(str(RUNNER))
    assert namespace["INDEX_NAMES"] == tuple(
        f"raw_event_dedupe_h{value:02d}_pkey"
        for value in range(16)
    )
    assert namespace["TABLE_NAMES"] == tuple(
        f"raw_event_dedupe_h{value:02d}"
        for value in range(16)
    )


def test_v4_dedupe_pk_reindex_runner_is_sequential_and_concurrent() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    for marker in (
        'REINDEX INDEX CONCURRENTLY "{index_name}"',
        "DEFAULT_STATEMENT_TIMEOUT_SECONDS = 1200",
        "MIN_TRANSIENT_FREE_BYTES = 2 * 1024**3",
        "TRANSIENT_INDEX_MULTIPLIER = 4",
        "headroom_ok",
        "invalid_indexes_after",
        "bytes_reclaimed",
        "total_bytes_reclaimed",
        "--execute is required",
    ):
        assert marker in source

    assert "ThreadPoolExecutor" not in source
    assert "asyncio.gather" not in source
    assert "multiprocessing" not in source


def test_v4_dedupe_pk_reindex_runner_fails_closed_on_index_health() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    for marker in (
        '"is_primary", "is_unique", "is_valid", "is_ready"',
        "required primary index missing",
        "primary index table mismatch",
        "primary index health mismatch",
        "invalid dedupe indexes remain after rebuild",
        '_write_evidence(evidence_path, payload)',
    ):
        assert marker in source


def test_v4_dedupe_pk_reindex_rollout_has_two_readiness_gates() -> None:
    source = ROLLOUT.read_text(encoding="utf-8")
    assert source.count("_require_preflight_readiness(") >= 3
    for marker in (
        'evidence_dir / "pre-readiness.json"',
        'evidence_dir / "mutation-readiness.json"',
        'evidence_dir / "post-readiness.json"',
        "_require_post_index_integrity(post_readiness)",
    ):
        assert marker in source


def test_v4_dedupe_pk_reindex_rollout_preserves_core_services() -> None:
    source = ROLLOUT.read_text(encoding="utf-8")
    for marker in (
        "RECORDER_UNIT = \"bp-recorder.service\"",
        "V3_PREDICTOR_UNIT = \"bp-v3-frozen-predictor.service\"",
        "V3_EXECUTION_UNIT = \"bp-v3-paper-execution.service\"",
        "_require_core_unchanged(core_before)",
        '"main_pid"',
        '"n_restarts"',
    ):
        assert marker in source

    for forbidden in (
        'systemctl", "stop", RECORDER_UNIT',
        'systemctl", "stop", V3_PREDICTOR_UNIT',
        'systemctl", "stop", V3_EXECUTION_UNIT',
        '"restart"',
        "pg_terminate_backend",
    ):
        assert forbidden not in source


def test_v4_dedupe_pk_reindex_rollout_restores_maintenance_timer() -> None:
    source = ROLLOUT.read_text(encoding="utf-8")
    for marker in (
        '_systemctl("stop", MAINTENANCE_TIMER)',
        "timer_stopped = True",
        '"systemctl",\n                "start",\n                MAINTENANCE_TIMER',
        '"maintenance_timer_restored"',
        "REQUIRED_TIMERS",
        "_require_timer_active_enabled(timer)",
    ):
        assert marker in source


def test_v4_dedupe_pk_reindex_rollout_records_before_after_effectiveness_evidence() -> None:
    source = ROLLOUT.read_text(encoding="utf-8")
    for marker in (
        'evidence_dir / "pre-index-health.json"',
        'evidence_dir / "post-index-health.json"',
        'evidence_dir / "pre-commit-lag.json"',
        'evidence_dir / "post-commit-lag.json"',
        '"effectiveness_requires_review": True',
        '"total_bytes_reclaimed"',
    ):
        assert marker in source


def test_v4_dedupe_pk_reindex_rollout_requires_research_zero_money() -> None:
    source = ROLLOUT.read_text(encoding="utf-8")
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


def test_v4_dedupe_pk_reindex_helper_requires_exact_fresh_approval() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for marker in (
        "PHASE14_V4_DEDUPE_PK_REINDEX_HELPER_HEAD",
        "PHASE14_V4_DEDUPE_PK_REINDEX_DEPLOYED_HEAD",
        "I_APPROVE_PHASE14_V4_DEDUPE_PK_REINDEX:",
        "local_helper_head_mismatch",
        "remote_main_changed",
        "production_approval_mismatch",
        "52b4355d6f077373b873f7a6f42bc37a20ddbc7b",
    ):
        assert marker in source


def test_v4_dedupe_pk_reindex_helper_streams_without_checkout_mutation() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for marker in (
        "run_v4_dedupe_pk_reindex_rollout.py",
        "reindex_v4_dedupe_primary_indexes.py",
        "report_v4_dedupe_reindex_readiness.py",
        "report_v4_dedupe_index_health.py",
        "report_v4_recorder_commit_lag.py",
        "RECORDER_REMAINS_ACTIVE=true",
        "V3_REMAINS_ACTIVE=true",
        "MAINTENANCE_TIMER_TEMPORARILY_QUIESCED=true",
    ):
        assert marker in source

    for forbidden in (
        'git -C "$REPO" checkout',
        "systemctl restart",
        "pg_terminate_backend",
        "DROP INDEX",
        "VACUUM ",
        "DELETE FROM raw_event_dedupe",
    ):
        assert forbidden not in source


def test_v4_dedupe_pk_reindex_helper_streams_remote_script_over_stdin() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for marker in (
        'printf \'%s\' "$REMOTE" | \\',
        'REMOTE_SCRIPT_PATH="$(mktemp /tmp/bp-v4-dedupe-pk-reindex.',
        'cat > "$REMOTE_SCRIPT_PATH"',
        'sudo bash "$REMOTE_SCRIPT_PATH"',
        'PIPE_RC=("${PIPESTATUS[@]}")',
        "grep -Eq '^PHASE14_V4_DEDUPE_PK_REINDEX_GATE=(PASS|FAIL)$'",
        "remote_terminal_marker_missing",
        "remote_script_stream_failed",
        "remote_output_capture_failed",
    ):
        assert marker in source

    for forbidden in (
        'REMOTE_B64="$(printf',
        '$REMOTE_B64',
        '--command="printf \'%s\'',
    ):
        assert forbidden not in source
