from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts/deploy/phase14_concurrent_partition_retirement_rollout_cloudshell.sh"
CI = ROOT / ".github/workflows/ci.yml"


def _content() -> str:
    return HELPER.read_text(encoding="utf-8")


def test_rollout_gate_binds_exact_production_candidate_and_requires_explicit_approval() -> None:
    content = _content()

    for required in (
        "PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_HELPER_HEAD",
        "PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_APPROVAL",
        "I_APPROVE_PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT",
        "production_approval_mismatch",
        "7c3af78da1922a0e5187c24b799951130cc98887",
        "ed7d930c69e417dda388b0cb62b3a543a4b8134f",
        "ops/phase14-concurrent-partition-retirement-candidate",
        "phase14-partitioned-storage-rollout-20260909T070219Z.json",
        "f33a28f5306e46c509b0000a176d226c079aa2d160d595095ca228118542ce19",
        "candidate_scope_mismatch",
        "candidate_runtime_script_blob_not_exact_main",
        "candidate_maintenance_blob_not_exact_main",
        "candidate_partitioned_blob_not_exact_main",
        "candidate_test_blob_not_exact_main",
        "scripts/storage_maintenance.py",
        "src/bp_engine/storage/maintenance.py",
        "src/bp_engine/storage/partitioned_raw.py",
        "tests/storage/test_partitioned_raw_postgres.py",
    ):
        assert required in content

    assert 'git -C "$REPO" checkout --detach "$CANDIDATE_HEAD"' in content
    assert "checkout --detach --force" not in content


def test_rollout_gate_requires_healthy_research_runtime_and_live_v3_v4_observation() -> None:
    content = _content()

    for required in (
        "mode_not_research",
        "live_trading_enabled",
        "max_trade_size_nonzero",
        "max_daily_loss_nonzero",
        "automatic_promotion must remain false",
        "bp-recorder.service",
        "bp-v3-frozen-predictor.service",
        "bp-v3-paper-execution.service",
        "bp-v4-forward-coverage.timer",
        "bp-storage-maintenance.timer",
        "bp-storage-disk-health.timer",
        "bp-v2-forward-coverage.timer",
        "storage health is not ok",
        "maintenance_fresh",
        "current_partition_present",
        "retention_current",
        "raw retirement leftovers present",
        "unit_fragment_mismatch",
        "unit_dropins_present",
        "recorder_environment_file_mismatch",
        "maintenance_environment_file_mismatch",
    ):
        assert required in content

    assert "LIVE_TRADING_ENABLED=true" not in content
    assert "MAX_TRADE_SIZE_USD=1" not in content


def test_rollout_gate_acceptance_must_exercise_real_partition_retirement() -> None:
    content = _content()

    for required in (
        "no_eligible_partition_for_acceptance",
        "run_acceptance_maintenance",
        "maintenance_did_not_exercise_partition_retirement",
        "partitions_retired",
        "dedupe_rows_removed",
        "recorder_main_pid_changed",
        "recorder_restarted_during_maintenance",
        "v3_predictor_pid_changed",
        "v3_execution_pid_changed",
        "scripts/soak_report.py",
        "backpressure recorded for",
        "dashboard left RESEARCH mode",
        "gate_b_artifacts_changed",
        "PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_GATE=PASS",
        "phase14-concurrent-partition-retirement-rollout-",
    ):
        assert required in content

    assert 'systemctl restart "$RECORDER_UNIT"' not in content
    assert "evaluate-holdout" not in content


def test_rollout_gate_reconciles_candidate_storage_before_old_checkout_on_failure() -> None:
    content = _content()

    for required in (
        "PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_ROLLBACK=START",
        "reconcile_candidate_storage",
        'systemctl stop "$V3_EXECUTION"',
        'systemctl stop "$V3_PREDICTOR"',
        'systemctl stop "$RECORDER_UNIT"',
        'git -C "$REPO" checkout --detach "$FROM_HEAD"',
        "PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_ROLLBACK=COMPLETE",
        "PHASE14_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_ROLLBACK=INCOMPLETE_RECONCILIATION_REQUIRED",
    ):
        assert required in content

    reconcile_at = content.index("if reconcile_candidate_storage; then")
    old_checkout_at = content.index('git -C "$REPO" checkout --detach "$FROM_HEAD"')
    assert reconcile_at < old_checkout_at


def test_ci_syntax_checks_concurrent_partition_retirement_rollout_gate() -> None:
    ci = CI.read_text(encoding="utf-8")
    assert (
        "bash -n scripts/deploy/phase14_concurrent_partition_retirement_rollout_cloudshell.sh"
        in ci
    )
