from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase14_v4_compact_dedupe_runtime_cutover_cloudshell.sh"
)


def test_compact_dedupe_runtime_cutover_has_valid_bash() -> None:
    subprocess.run(["bash", "-n", str(HELPER)], check=True)


def test_compact_dedupe_runtime_cutover_requires_exact_fresh_approval() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for marker in (
        "PHASE14_V4_COMPACT_DEDUPE_RUNTIME_HELPER_HEAD",
        "PHASE14_V4_COMPACT_DEDUPE_RUNTIME_STAGE",
        "PHASE14_V4_COMPACT_DEDUPE_RUNTIME_FROM_HEAD",
        "PHASE14_V4_COMPACT_DEDUPE_RUNTIME_TARGET_HEAD",
        "PHASE14_V4_COMPACT_DEDUPE_RUNTIME_TARGET_BRANCH",
        "I_APPROVE_PHASE14_V4_COMPACT_DEDUPE_RUNTIME_CUTOVER:",
        "local_helper_head_mismatch",
        "remote_main_changed",
        "target_branch_head_changed",
        "production_approval_mismatch",
    ):
        assert marker in source


def test_compact_dedupe_runtime_cutover_binds_writer_and_steady_scope() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for marker in (
        '[[ "$STAGE" == "writer" || "$STAGE" == "steady" ]]',
        "src/bp_engine/storage/recorder.py",
        "ON CONFLICT (dedupe_key) DO NOTHING",
        "ON CONFLICT DO NOTHING",
        "src/bp_engine/storage/partitioned_raw.py",
        "tests/storage/test_partitioned_raw_postgres.py",
        "_compact_dedupe_indexes_healthy",
        "partitioned dedupe uniqueness contract is missing or unhealthy",
        "target_not_descendant_of_from_head",
    ):
        assert marker in source


def test_compact_dedupe_runtime_cutover_is_rollback_capable() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for marker in (
        "ROLLBACK_ARMED=0",
        "ROLLBACK_ARMED=1",
        "rollback()",
        "PHASE14_V4_COMPACT_DEDUPE_RUNTIME_ROLLBACK=START",
        "PHASE14_V4_COMPACT_DEDUPE_RUNTIME_ROLLBACK=COMPLETE",
        'systemctl restart "$RECORDER_UNIT"',
        'phase14_prospective_runtime_install.sh" "$FROM_HEAD"',
    ):
        assert marker in source


def test_compact_dedupe_runtime_cutover_preserves_safety_boundary() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for marker in (
        '[[ "$(read_env MODE)" == "research" ]]',
        '[[ "$(read_env LIVE_TRADING_ENABLED)" == "false" ]]',
        '[[ "$(read_env MAX_TRADE_SIZE_USD)" == "0" ]]',
        '[[ "$(read_env MAX_DAILY_LOSS_USD)" == "0" ]]',
        "automatic_promotion must remain false",
        "bp-v3-frozen-predictor.service",
        "bp-v3-paper-execution.service",
        "bp-live-predictor.service",
        "bp-prospective-outcomes.service",
        "bp-storage-maintenance.timer",
        "bp-storage-disk-health.timer",
        "bp-v2-forward-coverage.timer",
        "bp-v4-forward-coverage.timer",
        "post_restart_soak_failed",
        "dashboard left RESEARCH mode",
    ):
        assert marker in source


def test_compact_dedupe_runtime_cutover_does_not_mutate_schema() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for forbidden in (
        "CREATE UNIQUE INDEX",
        "DROP CONSTRAINT",
        "ALTER TABLE raw_event_dedupe",
        "DROP INDEX",
        "REINDEX ",
        "VACUUM ",
        "pg_terminate_backend",
    ):
        assert forbidden not in source

    assert "DATABASE_SCHEMA_MUTATION=false" in source
