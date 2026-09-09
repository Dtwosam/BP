import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase14_v2_forward_coverage_restore_gate_cloudshell.sh"
)

def test_v2_forward_restore_gate_is_existing_runtime_only_and_rollback_capable() -> None:
    assert HELPER.is_file(), HELPER
    content = HELPER.read_text(encoding="utf-8")

    required = (
        "PHASE14_V2_FORWARD_RESTORE_HELPER_HEAD",
        "PHASE14_V2_FORWARD_RESTORE_DEPLOYED_HEAD",
        "PHASE14_V2_FORWARD_RESTORE_PROJECT",
        "PHASE14_V2_FORWARD_RESTORE_ZONE",
        "PHASE14_V2_FORWARD_RESTORE_VM",
        "PHASE14_V2_FORWARD_RESTORE_ENV_FILE",
        "PHASE14_V2_FORWARD_RESTORE_STORAGE_EVIDENCE",
        "PHASE14_V2_FORWARD_RESTORE_STORAGE_EVIDENCE_SHA256",
        "local_helper_head_mismatch",
        "local_working_tree_dirty",
        "remote_main_changed",
        "unexpected_deployed_head",
        "unexpected_deployed_checkout_change",
        "recorder_unit_not_active",
        "recorder_config_worker_count_not_4",
        "research_zero_money_boundary_not_satisfied",
        "storage_evidence_sha256_mismatch",
        'payload.get("status") != "ok"',
        'payload.get("storage_mode") != "partitioned"',
        '"maintenance_fresh"',
        '"current_partition_present"',
        '"retention_current"',
        "bp-recorder.service",
        "bp-postgres.service",
        "bp-dashboard-api.service",
        "bp-dashboard-web.service",
        "bp-paper-execution.service",
        "bp-live-predictor.service",
        "bp-prospective-outcomes.service",
        "bp-storage-maintenance.timer",
        "bp-storage-disk-health.timer",
        "bp-v2-forward-coverage.service",
        "bp-v2-forward-coverage.timer",
        "collector_service_fragment_mismatch",
        "collector_timer_fragment_mismatch",
        "collector_service_dropins_present",
        "collector_timer_dropins_present",
        'systemctl start "$SERVICE_UNIT"',
        'systemctl enable --now "$TIMER_UNIT"',
        "collector_timer_not_enabled",
        "collector_timer_not_active",
        "future_cutoff_violation_count",
        "policy_selected",
        "automatic_promotion",
        "coverage_row_count",
        "coverage_market_count",
        "PHASE14_V2_FORWARD_RESTORE_ROLLBACK=START",
        "PHASE14_V2_FORWARD_RESTORE_ROLLBACK=COMPLETE",
        "PHASE14_V2_FORWARD_RESTORE_GATE=PASS",
        "/var/lib/bp/evidence/phase14-v2-forward-coverage-restore-",
    )
    for marker in required:
        assert marker in content

    forbidden = (
        'git -C "$REPO" fetch',
        'git -C "$REPO" checkout',
        'git -C "$REPO" reset',
        "ensure_storage_indexes.py",
        'install -o root -g root -m 0644 "$REPO/deploy/',
        'systemctl restart "$RECORDER_UNIT"',
        "LIVE_TRADING_ENABLED=true",
        "MAX_TRADE_SIZE_USD=1",
        "MAX_DAILY_LOSS_USD=1",
    )
    for marker in forbidden:
        assert marker not in content

    for unit in (
        "bp-recorder.service",
        "bp-postgres.service",
        "bp-dashboard-api.service",
        "bp-dashboard-web.service",
        "bp-paper-execution.service",
        "bp-live-predictor.service",
        "bp-prospective-outcomes.service",
        "bp-storage-maintenance.timer",
        "bp-storage-disk-health.timer",
    ):
        assert f"systemctl restart {unit}" not in content
        assert f'systemctl restart "{unit}"' not in content
        assert f"systemctl stop {unit}" not in content
        assert f'systemctl stop "{unit}"' not in content


def test_v2_forward_restore_gate_has_clean_bash_syntax() -> None:
    result = subprocess.run(
        ["bash", "-n", str(HELPER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == "", result.stderr
