from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase14_v4_compact_recorder_latency_rollout_cloudshell.sh"
)

FROM_HEAD = "a694c2299cd34f0b2ee92ded4a4da1643eff0604"
CANDIDATE_HEAD = "4388314300afb9a2b8ee9cfc647c7b0f12dbff1a"
CANDIDATE_BRANCH = "ops/v4-compact-recorder-latency-candidate-20261006"
SHADOW_RUN_ID = "v4-fresh-book-shadow-20261006T185619Z-271db613e003"


def _source() -> str:
    return HELPER.read_text(encoding="utf-8")


def test_compact_recorder_latency_rollout_has_valid_bash() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(HELPER)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_compact_recorder_latency_rollout_is_exact_candidate_and_approval_bound() -> None:
    source = _source()
    for marker in (
        FROM_HEAD,
        CANDIDATE_HEAD,
        CANDIDATE_BRANCH,
        "PHASE14_V4_COMPACT_RECORDER_LATENCY_ROLLOUT_HELPER_HEAD",
        "PHASE14_V4_COMPACT_RECORDER_LATENCY_ROLLOUT_APPROVAL",
        "I_APPROVE_PHASE14_V4_COMPACT_RECORDER_LATENCY_ROLLOUT",
        SHADOW_RUN_ID,
        "candidate_branch_changed",
        "candidate_scope_mismatch",
        "candidate_blob_not_exact_main",
        "production_approval_mismatch",
    ):
        assert marker in source
    assert source.index("production_approval_mismatch") < source.index(
        "gcloud compute ssh"
    )


def test_compact_recorder_latency_rollout_scope_is_four_validated_files() -> None:
    source = _source()
    for path in (
        "src/bp_engine/recorder/writer.py",
        "src/bp_engine/storage/partitioned_raw.py",
        "tests/recorder/test_writer.py",
        "tests/storage/test_partitioned_raw_postgres.py",
    ):
        assert path in source
    assert 'git diff --name-only "$FROM_HEAD" "$CANDIDATE_HEAD"' in source


def test_compact_recorder_latency_rollout_preserves_compact_dedupe_contract() -> None:
    source = _source()
    for marker in (
        "require_compact_dedupe_complete",
        "COMPACT_DEDUPE_COMPLETE=true",
        "LEGACY_DEDUPE_PARENT_PK_PRESENT=false",
        "COMPACT_DIGEST_INDEX_COUNT=16",
        "raw_event_dedupe_pkey",
        "raw_event_dedupe_h%_digest_uidx",
        "expected 16 dedupe children",
    ):
        assert marker in source
    assert "CREATE INDEX" not in source
    assert "DROP INDEX" not in source
    assert "REINDEX" not in source


def test_compact_recorder_latency_rollout_changes_only_batch_500_to_100() -> None:
    source = _source()
    for marker in (
        "EXPECTED_BATCH_SIZE=500",
        "TARGET_BATCH_SIZE=100",
        "EXPECTED_QUEUE_MAXSIZE=50000",
        "recorder writer workers must equal 4",
        "recorder flush interval must equal 0.25",
        'set_recorder_batch_size "$TARGET_BATCH_SIZE"',
    ):
        assert marker in source


def test_compact_recorder_latency_rollout_requires_strict_visibility_acceptance() -> None:
    source = _source()
    for marker in (
        "fewer than 40/80 samples saw a committed row",
        "p95 committed-row age exceeds 2s",
        "timestamp-window readiness below 50%",
        'payload.get("query_timeout_count", -1)',
        'payload.get("query_failure_count", -1)',
        "run_visibility_acceptance",
        "run_soak",
    ):
        assert marker in source


def test_compact_recorder_latency_rollout_preserves_active_shadow_pid() -> None:
    source = _source()
    for marker in (
        'SHADOW_UNIT="$EXPECTED_SHADOW_UNIT"',
        "require_shadow_independent_of_recorder",
        "require_shadow_unchanged",
        'require_shadow_unchanged "pre_mutation"',
        'require_shadow_unchanged "recorder_stopped"',
        'require_shadow_unchanged "recorder_restarted"',
        'require_shadow_unchanged "post_visibility_acceptance"',
        'require_shadow_unchanged "post_timer_restore"',
        "SHADOW_RESTARTED=false",
        "shadow_preserved_without_restart",
    ):
        assert marker in source
    assert 'systemctl stop "$SHADOW_UNIT"' not in source
    assert 'systemctl start "$SHADOW_UNIT"' not in source
    assert 'systemctl restart "$SHADOW_UNIT"' not in source


def test_compact_recorder_latency_rollout_keeps_fast_live_closed() -> None:
    source = _source()
    for marker in (
        "bp-phase15-fast-live-source.service",
        "fast_live_source_active",
        "fast_live_source_enabled",
        "fast_live_source_active_after_rollout",
        "fast_live_source_enabled_after_rollout",
    ):
        assert marker in source
    assert 'systemctl start "$FAST_LIVE_SOURCE"' not in source


def test_compact_recorder_latency_rollout_rolls_back_checkout_env_and_chain() -> None:
    source = _source()
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
        "SHADOW_ACTIVE=",
        "SHADOW_PID=",
        "SHADOW_RESTARTS=",
    ):
        assert marker in rollback
    assert 'systemctl stop "$SHADOW_UNIT"' not in rollback
    assert 'systemctl start "$SHADOW_UNIT"' not in rollback


def test_compact_recorder_latency_rollout_preserves_zero_money_boundary() -> None:
    source = _source()
    for marker in (
        '[[ "$mode" == "research" ]]',
        '[[ "$live" == "false" ]]',
        '[[ "$trade" == "0" ]]',
        '[[ "$loss" == "0" ]]',
        'echo "LIVE_TRADING_ENABLED=false"',
        'echo "MAX_TRADE_SIZE_USD=0"',
        'echo "MAX_DAILY_LOSS_USD=0"',
        "require_automatic_promotion_false",
    ):
        assert marker in source
    assert "LIVE_TRADING_ENABLED=true" not in source
