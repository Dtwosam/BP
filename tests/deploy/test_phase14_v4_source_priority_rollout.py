from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase14_v4_source_priority_rollout_cloudshell.sh"
)

FROM_HEAD = "a694c2299cd34f0b2ee92ded4a4da1643eff0604"
CANDIDATE_HEAD = "bd815c42a2c8d7be00455f2e24279c5d380e7096"
CANDIDATE_BRANCH = "ops/v4-source-priority-candidate-20261006"
SHADOW_RUN_ID = "v4-fresh-book-shadow-20261006T185619Z-271db613e003"


def _source() -> str:
    return HELPER.read_text(encoding="utf-8")


def test_v4_source_priority_rollout_has_valid_bash() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(HELPER)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_v4_source_priority_rollout_is_exact_candidate_and_approval_bound() -> None:
    source = _source()
    for marker in (
        FROM_HEAD,
        CANDIDATE_HEAD,
        CANDIDATE_BRANCH,
        "PHASE14_V4_SOURCE_PRIORITY_ROLLOUT_HELPER_HEAD",
        "PHASE14_V4_SOURCE_PRIORITY_ROLLOUT_APPROVAL",
        "I_APPROVE_PHASE14_V4_SOURCE_PRIORITY_ROLLOUT",
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


def test_v4_source_priority_rollout_has_local_only_preflight() -> None:
    source = _source()
    for marker in (
        "PHASE14_V4_SOURCE_PRIORITY_ROLLOUT_PREFLIGHT_ONLY",
        "PHASE14_V4_SOURCE_PRIORITY_ROLLOUT_PREFLIGHT=PASS",
        "EXPECTED_APPROVAL=$EXPECTED_APPROVAL",
        "PRODUCTION_MUTATION=false",
        "GCLOUD_CONTACT=false",
        "preflight_only_invalid",
    ):
        assert marker in source

    preflight = source.index('if [[ "$PREFLIGHT_ONLY" == "true" ]]')
    approval = source.index('[[ "$APPROVAL" == "$EXPECTED_APPROVAL" ]]')
    gcloud = source.index("command -v gcloud")
    assert preflight < approval < gcloud
    assert source.index('echo "GCLOUD_CONTACT=false"') < gcloud
    assert source.index("exit 0", preflight) < approval


def test_v4_source_priority_rollout_scope_is_eight_validated_files() -> None:
    source = _source()
    for path in (
        "src/bp_engine/recorder/writer.py",
        "src/bp_engine/storage/partitioned_raw.py",
        "src/bp_engine/recorder/service.py",
        "src/bp_engine/config.py",
        "tests/recorder/test_writer.py",
        "tests/storage/test_partitioned_raw_postgres.py",
        "tests/recorder/test_recorder_service.py",
        "tests/test_config.py",
    ):
        assert path in source
    assert 'git diff --name-only "$FROM_HEAD" "$CANDIDATE_HEAD"' in source


def test_v4_source_priority_rollout_preserves_compact_dedupe_contract() -> None:
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


def test_v4_source_priority_rollout_preserves_batch_and_writer_budget() -> None:
    source = _source()
    for marker in (
        "EXPECTED_BATCH_SIZE=500",
        "EXPECTED_QUEUE_MAXSIZE=50000",
        "EXPECTED_WRITER_WORKERS=4",
        "EXPECTED_PRIORITY_QUEUE_MAXSIZE=5000",
        "EXPECTED_PRIORITY_BATCH_SIZE=20",
        "recorder writer workers must equal 4",
        "recorder flush interval must equal 0.25",
        "RECORDER_PRIORITY_WRITER_WORKERS=1",
        "RECORDER_BULK_WRITER_WORKERS=3",
        "RECORDER_TOTAL_WRITER_WORKERS=4",
        "require_priority_config",
    ):
        assert marker in source
    assert "TARGET_BATCH_SIZE" not in source
    assert "set_recorder_batch_size" not in source


def test_v4_source_priority_rollout_binds_priority_source_contract() -> None:
    source = _source()
    for marker in (
        "_is_v4_source_time_event",
        "_RoutedBufferedEventSink",
        '"writer_priority"',
        '"writer_bulk"',
        "priority_source_contract_missing",
    ):
        assert marker in source


def test_v4_source_priority_rollout_requires_strict_visibility_acceptance() -> None:
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


def test_v4_source_priority_rollout_preserves_active_shadow_pid() -> None:
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


def test_v4_source_priority_rollout_keeps_fast_live_closed() -> None:
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


def test_v4_source_priority_rollout_rolls_back_checkout_and_chain() -> None:
    source = _source()
    rollback = source[source.index("rollback() {") : source.index("cleanup() {")]
    for marker in (
        'systemctl stop "$V3_EXECUTION"',
        'systemctl stop "$V3_PREDICTOR"',
        'systemctl stop "$RECORDER_UNIT"',
        'git -C "$REPO" checkout --detach --force "$FROM_HEAD"',
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


def test_v4_source_priority_rollout_preserves_zero_money_boundary() -> None:
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
