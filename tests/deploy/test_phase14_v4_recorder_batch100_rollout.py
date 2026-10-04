from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase14_v4_recorder_batch100_rollout_cloudshell.sh"
)


def read_helper() -> str:
    return HELPER.read_text(encoding="utf-8")


def test_batch100_rollout_helper_has_valid_bash() -> None:
    subprocess.run(["bash", "-n", str(HELPER)], check=True)


def test_batch100_rollout_is_exact_candidate_and_approval_bound() -> None:
    source = read_helper()
    for marker in (
        "52b4355d6f077373b873f7a6f42bc37a20ddbc7b",
        "444fdf7958e3825cb0a32250d9abfd8196e19fb1",
        "ops/phase14-v4-recorder-visibility-candidate",
        "PHASE14_V4_BATCH100_ROLLOUT_HELPER_HEAD",
        "PHASE14_V4_BATCH100_ROLLOUT_APPROVAL",
        "I_APPROVE_PHASE14_V4_BATCH100_ROLLOUT",
        "candidate_branch_changed",
        "candidate_scope_mismatch",
        "candidate_blob_not_exact_main",
        "production_approval_mismatch",
    ):
        assert marker in source
    assert source.index("production_approval_mismatch") < source.index(
        "gcloud compute ssh"
    )


def test_batch100_rollout_requires_exact_live_recorder_config() -> None:
    source = read_helper()
    for marker in (
        "EXPECTED_BATCH_SIZE=500",
        "TARGET_BATCH_SIZE=100",
        "EXPECTED_QUEUE_MAXSIZE=50000",
        "recorder queue maxsize must equal",
        "recorder batch size must equal",
        "recorder writer workers must equal 4",
        "recorder flush interval must equal 0.25",
        'require_recorder_config "$EXPECTED_BATCH_SIZE"',
        'require_recorder_config "$TARGET_BATCH_SIZE"',
    ):
        assert marker in source


def test_batch100_rollout_changes_only_batch_env_and_frozen_candidate() -> None:
    source = read_helper()
    for path in (
        "src/bp_engine/recorder/writer.py",
        "src/bp_engine/storage/partitioned_raw.py",
        "tests/recorder/test_writer.py",
        "tests/storage/test_partitioned_raw_postgres.py",
    ):
        assert path in source
    assert 'git diff --name-only "$FROM_HEAD" "$CANDIDATE_HEAD"' in source
    assert 'set_recorder_batch_size "$TARGET_BATCH_SIZE"' in source
    assert "RECORDER_BATCH_SIZE=" in source
    assert "RECORDER_WRITER_WORKERS=6" not in source
    assert "RECORDER_WRITER_WORKERS=8" not in source
    assert "RECORDER_FLUSH_INTERVAL_SECONDS=0.1" not in source


def test_batch100_rollout_quiesces_maintenance_before_storage_snapshot() -> None:
    source = read_helper()
    mutation = source[source.index("MUTATION_STARTED=1") :]
    stop_at = mutation.index('systemctl stop "$MAINTENANCE_TIMER"')
    idle_at = mutation.index(
        'wait_for_oneshot_idle_success "$MAINTENANCE_SERVICE" 3600'
    )
    storage_at = mutation.index('run_storage_health "$DISK_BEFORE"')
    recorder_stop_at = mutation.index('systemctl stop "$RECORDER_UNIT"')

    assert stop_at < idle_at < storage_at < recorder_stop_at
    assert 'require_timer_active_enabled "$MAINTENANCE_TIMER"' in source
    assert 'require_timer_enabled_inactive "$MAINTENANCE_TIMER"' in mutation


def test_batch100_rollout_restarts_chain_in_dependency_order() -> None:
    source = read_helper()
    start = source[
        source.index("start_chain() {") : source.index("run_visibility_acceptance() {")
    ]
    assert start.index('systemctl start "$RECORDER_UNIT"') < start.index(
        'systemctl start "$V3_PREDICTOR"'
    )
    assert start.index('systemctl start "$V3_PREDICTOR"') < start.index(
        'systemctl start "$V3_EXECUTION"'
    )


def test_batch100_rollout_has_v4_visibility_acceptance() -> None:
    source = read_helper()
    for marker in (
        "fewer than 40/80 samples saw a committed row",
        "p95 committed-row age exceeds 2s",
        "timestamp-window readiness below 50%",
        'payload.get("query_timeout_count", -1)',
        'payload.get("query_failure_count", -1)',
        "run_visibility_acceptance",
        "run_soak",
        "verify_dashboard_safety",
    ):
        assert marker in source


def test_batch100_rollout_rolls_back_exact_env_checkout_and_services() -> None:
    source = read_helper()
    rollback = source[source.index("rollback() {") : source.index("cleanup() {")]
    for marker in (
        'systemctl stop "$V3_EXECUTION"',
        'systemctl stop "$V3_PREDICTOR"',
        'systemctl stop "$RECORDER_UNIT"',
        'git -C "$REPO" checkout --detach --force "$FROM_HEAD"',
        'cp -a "$BACKUP_DIR/bp.env" "$ENV_FILE"',
        'systemctl start "$RECORDER_UNIT"',
        'systemctl start "$V3_PREDICTOR"',
        'systemctl start "$V3_EXECUTION"',
        'systemctl start "$MAINTENANCE_TIMER"',
        'RECORDER_BATCH_SIZE=$(read_env "$ENV_FILE" RECORDER_BATCH_SIZE)',
    ):
        assert marker in rollback


def test_batch100_rollout_restores_automatic_maintenance_on_pass() -> None:
    source = read_helper()
    visibility_at = source.index("run_visibility_acceptance")
    timer_start_at = source.rindex('systemctl start "$MAINTENANCE_TIMER"')
    assert visibility_at < timer_start_at
    assert 'require_timer_active_enabled "$MAINTENANCE_TIMER"' in source
    assert 'echo "MAINTENANCE_TIMER_ACTIVE=active"' in source


def test_batch100_rollout_preserves_research_zero_money_boundary() -> None:
    source = read_helper()
    for marker in (
        '[[ "$mode" == "research" ]]',
        '[[ "$live" == "false" ]]',
        '[[ "$trade" == "0" ]]',
        '[[ "$loss" == "0" ]]',
        'echo "LIVE_TRADING_ENABLED=false"',
        'echo "MAX_TRADE_SIZE_USD=0"',
        'echo "MAX_DAILY_LOSS_USD=0"',
    ):
        assert marker in source
    assert "LIVE_TRADING_ENABLED=true" not in source


def test_batch100_rollout_uses_portable_base64() -> None:
    source = read_helper()
    assert "base64 | tr -d '\\n'" in source
    assert "base64 -w0" not in source


def test_batch100_rollout_prints_visibility_report_before_failed_rollback() -> None:
    source = read_helper()
    acceptance = source[
        source.index("run_visibility_acceptance() {") : source.index("rollback() {")
    ]
    assert 'echo "PHASE14_V4_BATCH100_VISIBILITY_ACCEPTANCE=FAIL" >&2' in acceptance
    assert 'cat "$VISIBILITY_FILE" >&2 || true' in acceptance
    assert acceptance.index("PHASE14_V4_BATCH100_VISIBILITY_ACCEPTANCE=FAIL") < acceptance.index(
        "return 1"
    )
