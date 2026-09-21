from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts" / "deploy" / "phase14_recorder_v3_recovery_gate_cloudshell.sh"


def read_helper() -> str:
    return HELPER.read_text(encoding="utf-8")


def test_recorder_v3_recovery_helper_has_clean_bash_syntax() -> None:
    subprocess.run(["bash", "-n", str(HELPER)], check=True)


def test_recorder_v3_recovery_is_sha_bound_before_cloud_contact() -> None:
    source = read_helper()
    assert "7c3af78da1922a0e5187c24b799951130cc98887" in source
    assert "PHASE14_RECORDER_V3_RECOVERY_HELPER_HEAD" in source
    assert "PHASE14_RECORDER_V3_RECOVERY_APPROVAL" in source
    assert "I_APPROVE_PHASE14_RECORDER_V3_RECOVERY" in source
    assert "production_approval_mismatch" in source
    assert source.index("production_approval_mismatch") < source.index("gcloud compute ssh")
    assert '[[ "$REMOTE_MAIN" == "$HELPER_HEAD" ]]' in source


def test_recorder_v3_recovery_preserves_four_writer_research_zero_money_contract() -> None:
    source = read_helper()
    for marker in (
        "require_research_zero_money",
        "require_automatic_promotion_false",
        "require_workers_four",
        "recorder writer workers must equal 4",
        '[[ "$mode" == "research" ]]',
        '[[ "$live" == "false" ]]',
        '[[ "$trade" == "0" ]]',
        '[[ "$loss" == "0" ]]',
        "LIVE_TRADING_ENABLED=false",
        "MAX_TRADE_SIZE_USD=0",
        "MAX_DAILY_LOSS_USD=0",
    ):
        assert marker in source
    assert 'install -o "$ENV_UID"' not in source
    assert 'install -o "$ENV_UID" -g "$ENV_GID"' not in source
    assert 'RECORDER_WRITER_WORKERS=4"' not in source
    assert "sed -i" not in source


def test_recorder_v3_recovery_validates_storage_units_and_frozen_activation() -> None:
    source = read_helper()
    for marker in (
        "run_storage_health",
        '"maintenance_fresh", "current_partition_present", "retention_current"',
        "validate_units",
        "unit_fragment_mismatch",
        "/var/lib/bp/runtime/v3-paper-current",
        "validate_v3_activation",
        "v3 activation/model digest mismatch",
        "v3-frozen-paper-v1",
        "paper-execution-v3-frozen-v1",
        "bp-storage-maintenance.timer",
        "bp-storage-disk-health.timer",
        "bp-v2-forward-coverage.timer",
        "bp-v4-forward-coverage.timer",
    ):
        assert marker in source


def test_recorder_v3_recovery_starts_only_the_expected_chain_and_rolls_back_stopped() -> None:
    source = read_helper()
    recorder_start = source.index('systemctl start "$RECORDER_UNIT"')
    predictor_start = source.index('systemctl start "$V3_PREDICTOR"')
    execution_start = source.index('systemctl start "$V3_EXECUTION"')
    assert recorder_start < predictor_start < execution_start

    rollback = source[source.index("rollback() {") : source.index("cleanup() {")]
    assert 'systemctl stop "$V3_EXECUTION"' in rollback
    assert 'systemctl stop "$V3_PREDICTOR"' in rollback
    assert 'systemctl stop "$RECORDER_UNIT"' in rollback

    assert 'git -C "$REPO" checkout' not in source
    assert 'git -C "$REPO" fetch' not in source
    assert "systemctl enable " not in source
    assert "systemctl disable " not in source


def test_recorder_v3_recovery_requires_stable_services_and_preserves_gate_b_artifacts() -> None:
    source = read_helper()
    for marker in (
        "natural_load_soak_failed",
        "dashboard_snapshot_unavailable",
        "recorder_pid_changed",
        "v3_predictor_pid_changed",
        "v3_execution_pid_changed",
        "recorder_restarted_during_recovery",
        "v3_predictor_restarted_during_recovery",
        "v3_execution_restarted_during_recovery",
        "gate_b_fingerprint",
        "gate_b_artifacts_changed",
        "phase14-recorder-v3-recovery-",
        "PHASE14_RECORDER_V3_RECOVERY_GATE=PASS",
    ):
        assert marker in source
