from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts/deploy/phase14_recorder_deadlock_recovery_gate_cloudshell.sh"
CI = ROOT / ".github/workflows/ci.yml"


def test_deadlock_recovery_gate_binds_exact_minimal_production_candidate() -> None:
    content = HELPER.read_text(encoding="utf-8")

    for required in (
        "PHASE14_RECORDER_DEADLOCK_RECOVERY_HELPER_HEAD",
        "895c6bd2f9409f16bf5d544b26b30e20ecbfe43a",
        "ops/phase14-storage-deadlock-recovery-candidate",
        "e9c7afc1536880e4612cb6e3d1a7282fa37c69f5",
        "phase14-partitioned-storage-rollout-20260909T070219Z.json",
        "f33a28f5306e46c509b0000a176d226c079aa2d160d595095ca228118542ce19",
        "local_helper_head_mismatch",
        "remote_main_changed",
        "candidate_branch_changed",
        "candidate_not_descendant_of_deployed_head",
        "candidate_scope_mismatch",
        "candidate_runtime_blob_not_exact_main",
        "candidate_test_blob_not_exact_main",
        "src/bp_engine/storage/partitioned_raw.py",
        "tests/storage/test_partitioned_raw_postgres.py",
    ):
        assert required in content

    assert 'git -C "$REPO" checkout --detach "$CANDIDATE_HEAD"' in content
    assert 'git -C "$REPO" checkout --detach "$FROM_HEAD"' in content
    assert 'checkout --detach "$HELPER_HEAD"' not in content
    assert "checkout --detach --force" not in content


def test_deadlock_recovery_gate_requires_existing_four_worker_safe_runtime() -> None:
    content = HELPER.read_text(encoding="utf-8")

    for required in (
        "writer_worker_setting_count_not_1",
        "writer_workers_not_4",
        "recorder_config_worker_count_not_4",
        'Settings(_env_file=sys.argv[1]).recorder_writer_workers',
        "mode_not_research",
        "live_trading_enabled",
        "max_trade_size_nonzero",
        "max_daily_loss_nonzero",
        "automatic_promotion must remain false",
        "recorder_not_inactive",
        "bp-storage-maintenance.timer",
        "bp-storage-disk-health.timer",
        "bp-v2-forward-coverage.timer",
        "storage_evidence_sha256_mismatch",
        'payload.get("status") != "ok"',
        'payload.get("storage_mode") != "partitioned"',
        '"maintenance_fresh"',
        '"current_partition_present"',
        '"retention_current"',
    ):
        assert required in content

    assert "RECORDER_WRITER_WORKERS=4" not in content
    assert 'install -o "$ENV_UID"' not in content
    assert "ENV_BACKUP" not in content


def test_deadlock_recovery_gate_proves_fix_under_active_recorder_load() -> None:
    content = HELPER.read_text(encoding="utf-8")

    for required in (
        'systemctl start "$RECORDER_UNIT"',
        'run_maintenance_cycle "prestart"',
        'run_maintenance_cycle "active_recorder"',
        "recorder_main_pid_changed_during_maintenance",
        "recorder_restarted_during_maintenance",
        "sleep 45",
        "scripts/soak_report.py",
        "required feeds missing post-recovery events",
        "required feed recorded backpressure",
        "dashboard left RESEARCH mode",
        "gate_b_fingerprint",
        "gate_b_artifacts_changed",
        '"holdout_touched": False',
        '"actions_performed": False',
        '"automatic_promotion": False',
        "/var/lib/bp/evidence",
        "phase14-recorder-deadlock-recovery-",
        "PHASE14_RECORDER_DEADLOCK_RECOVERY_GATE=PASS",
    ):
        assert required in content

    assert 'systemctl restart "$RECORDER_UNIT"' not in content
    assert "evaluate-holdout" not in content
    assert "run_v2_gate_b_research.py" not in content
    assert "LIVE_TRADING_ENABLED=true" not in content
    assert "MAX_TRADE_SIZE_USD=1" not in content


def test_deadlock_recovery_gate_rolls_back_checkout_and_keeps_recorder_stopped() -> None:
    content = HELPER.read_text(encoding="utf-8")

    for required in (
        "PHASE14_RECORDER_DEADLOCK_RECOVERY_ROLLBACK=START",
        "PHASE14_RECORDER_DEADLOCK_RECOVERY_ROLLBACK=COMPLETE",
        'systemctl stop "$RECORDER_UNIT"',
        'systemctl stop "$V2_TIMER" "$MAINTENANCE_TIMER" "$DISK_HEALTH_TIMER"',
        'git -C "$REPO" checkout --detach "$FROM_HEAD"',
        'systemctl start "$DISK_HEALTH_TIMER"',
        'systemctl start "$MAINTENANCE_TIMER"',
        'systemctl start "$V2_TIMER"',
    ):
        assert required in content

    for service in (
        "bp-postgres.service",
        "bp-dashboard-api.service",
        "bp-dashboard-web.service",
        "bp-paper-execution.service",
        "bp-live-predictor.service",
        "bp-prospective-outcomes.service",
    ):
        assert f"systemctl restart {service}" not in content
        assert f'systemctl restart "{service}"' not in content
        assert f"systemctl stop {service}" not in content
        assert f'systemctl stop "{service}"' not in content


def test_ci_syntax_checks_deadlock_recovery_gate() -> None:
    ci = CI.read_text(encoding="utf-8")
    assert (
        "bash -n scripts/deploy/phase14_recorder_deadlock_recovery_gate_cloudshell.sh"
        in ci
    )
