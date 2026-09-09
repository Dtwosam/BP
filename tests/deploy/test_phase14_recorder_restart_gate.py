from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts/deploy/phase14_recorder_restart_gate_cloudshell.sh"
CI = ROOT / ".github/workflows/ci.yml"


def test_recorder_restart_gate_is_storage_bound_restart_only_and_rollback_capable() -> None:
    content = HELPER.read_text(encoding="utf-8")

    for required in (
        "PHASE14_RECORDER_RESTART_HELPER_HEAD",
        "PHASE14_RECORDER_RESTART_DEPLOYED_HEAD",
        "PHASE14_RECORDER_RESTART_STORAGE_EVIDENCE",
        "PHASE14_RECORDER_RESTART_STORAGE_EVIDENCE_SHA256",
        "PHASE14_RECORDER_RESTART_WRITER_WORKERS",
        "local_helper_head_mismatch",
        "remote_main_changed",
        "storage_evidence_sha256_mismatch",
        'payload.get("status") != "ok"',
        'payload.get("storage_mode") != "partitioned"',
        '"maintenance_fresh"',
        '"current_partition_present"',
        '"retention_current"',
        "bp-storage-maintenance.timer",
        "bp-storage-disk-health.timer",
        "recorder_already_active",
        "RECORDER_WRITER_WORKERS=4",
        "sleep 45",
        "scripts/soak_report.py",
        "required feeds missing post-restart events",
        "required feed recorded backpressure",
        "PHASE14_RECORDER_RESTART_ROLLBACK=START",
        "PHASE14_RECORDER_RESTART_ROLLBACK=COMPLETE",
        "PHASE14_RECORDER_RESTART_GATE=PASS",
        "/var/lib/bp/evidence/phase14-recorder-restart-gate-",
    ):
        assert required in content

    assert 'systemctl restart "$RECORDER_UNIT"' in content
    assert 'systemctl stop "$RECORDER_UNIT"' in content

    for forbidden in (
        "git -C /opt/bp checkout",
        "phase14_prospective_runtime_install.sh",
        "LIVE_TRADING_ENABLED=true",
        "MAX_TRADE_SIZE_USD=1",
    ):
        assert forbidden not in content

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


def test_recorder_restart_gate_verifies_worker_config_without_proc_environ() -> None:
    content = HELPER.read_text(encoding="utf-8")

    for required in (
        'systemctl show -p EnvironmentFiles --value "$RECORDER_UNIT"',
        "recorder_environment_file_mismatch",
        "Settings(_env_file=sys.argv[1]).recorder_writer_workers",
        "recorder_config_worker_count_not_4",
        "RECORDER_CONFIG_WORKERS=$CONFIG_WORKERS",
    ):
        assert required in content

    assert '/proc/$MAIN_PID/environ' not in content
    assert "recorder_effective_worker_count_not_4" not in content


def test_ci_syntax_checks_recorder_restart_gate() -> None:
    ci = CI.read_text(encoding="utf-8")
    assert "bash -n scripts/deploy/phase14_recorder_restart_gate_cloudshell.sh" in ci
