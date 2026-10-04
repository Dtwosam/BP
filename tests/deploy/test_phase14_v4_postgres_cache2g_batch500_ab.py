from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase14_v4_postgres_cache2g_batch500_ab_cloudshell.sh"
)
RUNNER = ROOT / "scripts" / "run_v4_postgres_cache2g_batch500_ab.py"


def test_cache2g_batch500_ab_helper_has_valid_bash() -> None:
    subprocess.run(["bash", "-n", str(HELPER)], check=True)


def test_cache2g_batch500_ab_binds_exact_production_inputs() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for marker in (
        "EXPECTED_DEPLOYED_HEAD=52b4355d6f077373b873f7a6f42bc37a20ddbc7b",
        "EXPECTED_BATCH_SIZE=500",
        "BASELINE_SHARED_BUFFERS=128MB",
        "CANDIDATE_SHARED_BUFFERS=2GB",
        "I_APPROVE_PHASE14_V4_PG_CACHE2G_BATCH500_AB:",
        "local_helper_head_mismatch",
        "remote_main_changed",
        "production_approval_mismatch",
        "ALWAYS_RESTORE_BASELINE=true",
    ):
        assert marker in source


def test_cache2g_batch500_ab_does_not_change_batch_or_checkout() -> None:
    helper = HELPER.read_text(encoding="utf-8")
    runner = RUNNER.read_text(encoding="utf-8")
    combined = helper + runner

    assert "EXPECTED_BATCH_SIZE = 500" in runner
    assert "recorder_batch_size" in runner
    assert "TARGET_BATCH_SIZE=100" not in combined
    assert "RECORDER_BATCH_SIZE=100" not in combined
    assert "git checkout" not in combined
    assert "checkout --detach" not in combined
    assert "candidate_head" not in combined.lower()


def test_cache2g_batch500_ab_is_always_restore_experiment() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    for marker in (
        'BASELINE_SHARED_BUFFERS = "128MB"',
        'CANDIDATE_SHARED_BUFFERS = "2GB"',
        "WARMUP_SECONDS = 300",
        '"always_restore_baseline": True',
        'PHASE=candidate_recreate_postgres_2GB',
        'PHASE=restore_deployed_postgres_128MB',
        "compose_path=_deployed_compose(repo)",
        'summary["restored_baseline"] = True',
        '"environment_file_mutated": False',
        'print("PRODUCTION_FINAL_SHARED_BUFFERS=128MB")',
        'print("EXPERIMENT_PERSISTED=false")',
    ):
        assert marker in source

    success_at = source.index('summary["status"] = "success"')
    restore_at = source.index(
        'print("PHASE=restore_deployed_postgres_128MB"'
    )
    assert restore_at < success_at


def test_cache2g_batch500_ab_captures_paired_commit_lag() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    for marker in (
        "import report_v4_recorder_commit_lag as commit_lag",
        'evidence_dir / "baseline-commit-lag.json"',
        'evidence_dir / "candidate-commit-lag.json"',
        "def _report_comparison(",
        '"dedupe_insert"',
        '"commit_advancement_ratio"',
        '"lag_delta_seconds"',
        '"commit_lag_seconds"',
    ):
        assert marker in source


def test_cache2g_batch500_ab_evidence_connection_is_read_only() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    capture_start = source.index("def _capture_commit_lag(")
    capture_end = source.index("\ndef _report_comparison(", capture_start)
    capture = source[capture_start:capture_end]

    assert "default_transaction_read_only=on" in capture
    assert 'isolation_level="AUTOCOMMIT"' in capture
    for forbidden in (
        "INSERT ",
        "UPDATE ",
        "DELETE ",
        "REINDEX ",
        "pg_terminate_backend",
    ):
        assert forbidden not in capture


def test_cache2g_batch500_ab_preserves_research_zero_money_boundary() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    for marker in (
        '"MODE": "research"',
        '"LIVE_TRADING_ENABLED": "false"',
        '"MAX_TRADE_SIZE_USD": "0"',
        '"MAX_DAILY_LOSS_USD": "0"',
        "automatic_promotion must remain false",
    ):
        assert marker in source
    assert "LIVE_TRADING_ENABLED=true" not in source


def test_cache2g_batch500_ab_has_memory_guards() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    for marker in (
        "MIN_HOST_TOTAL_BYTES = 7 * 1024**3",
        "MIN_BASELINE_AVAILABLE_BYTES = 4 * 1024**3",
        "MIN_CANDIDATE_AVAILABLE_BYTES = 2 * 1024**3",
        "_require_memory(MIN_BASELINE_AVAILABLE_BYTES)",
        "_require_memory(",
        "MIN_CANDIDATE_AVAILABLE_BYTES",
    ):
        assert marker in source


def test_cache2g_batch500_ab_quiesces_and_restores_cycle_timers() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    for marker in (
        "CYCLE_TIMERS = (MAINTENANCE_TIMER, V2_TIMER, V4_TIMER)",
        "CYCLE_ONESHOTS = (MAINTENANCE_SERVICE, V2_SERVICE, V4_SERVICE)",
        "def _quiesce_cycle_timers()",
        "def _restore_cycle_timers()",
        "_wait_oneshot_idle_success(service, 3600)",
        "_restore_cycle_timers()",
    ):
        assert marker in source


def test_cache2g_batch500_ab_stops_core_before_postgres_recreate() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    candidate = source[source.index('print("PHASE=candidate_quiesce"') :]
    assert candidate.index("_stop_core_chain()") < candidate.index(
        "candidate_postgres_identity = _recreate_postgres("
    )
    assert candidate.index(
        "candidate_postgres_identity = _recreate_postgres("
    ) < candidate.index("_start_core_chain()")


def test_cache2g_batch500_ab_failure_path_restores_baseline() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    failure = source[source.index("except Exception as exc:") :]
    for marker in (
        'print("PHASE=emergency_restore"',
        "compose_path=_deployed_compose(repo)",
        "_recreate_postgres(",
        "_start_core_chain()",
        "_restore_cycle_timers()",
        "BASELINE_SHARED_BUFFERS",
        'summary["restored_baseline"] = restored',
    ):
        assert marker in failure


def test_cache2g_batch500_ab_helper_streams_exact_scripts() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for marker in (
        "scripts/run_v4_postgres_cache2g_batch500_ab.py",
        "scripts/report_v4_recorder_commit_lag.py",
        "docker-compose.prod.yml",
        '--candidate-compose "$tmp/docker-compose.prod.yml"',
        'cat > "$REMOTE_SCRIPT_PATH"',
        'sudo bash "$REMOTE_SCRIPT_PATH"',
        "PHASE14_V4_PG_CACHE2G_BATCH500_AB_GATE=(PASS|FAIL)",
        "remote_terminal_marker_missing",
    ):
        assert marker in source


def test_cache2g_batch500_ab_expands_files_array_correctly() -> None:
    source = HELPER.read_text(encoding="utf-8")
    assert 'for path in "${FILES[@]}"; do' in source
    assert 'for path in "$FILES[@]"; do' not in source


def test_cache2g_batch500_ab_uses_staged_compose_without_env_file_mutation() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    for marker in (
        'required = "shared_buffers=${POSTGRES_SHARED_BUFFERS:-128MB}"',
        'environment["POSTGRES_SHARED_BUFFERS"] = shared_buffers',
        '"--force-recreate"',
        '"--project-directory"',
        '"compose_project"',
        '"data_mount_source"',
        '"environment_file_mutated": False',
    ):
        assert marker in source
    assert "_replace_env_value(" not in source
    assert "_restore_env(" not in source


def test_cache2g_batch500_ab_preserves_compose_project_and_data_mount() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    for marker in (
        "def _discover_postgres_identity(",
        "def _current_project_postgres_identity(",
        'labels.get("com.docker.compose.project")',
        'mount.get("Destination") == "/var/lib/postgresql/data"',
        'identity["data_mount_source"] != expected_data_mount_source',
        '"postgres data mount changed during candidate recreation',
        '"restored postgres data mount mismatch"',
    ):
        assert marker in source
