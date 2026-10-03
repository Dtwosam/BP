from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase14_v4_recorder_visibility_recovery_cloudshell.sh"
)


def read_helper() -> str:
    return HELPER.read_text(encoding="utf-8")


def test_v4_visibility_recovery_helper_has_valid_bash() -> None:
    subprocess.run(["bash", "-n", str(HELPER)], check=True)


def test_v4_visibility_recovery_is_exact_head_and_approval_bound() -> None:
    source = read_helper()
    assert "52b4355d6f077373b873f7a6f42bc37a20ddbc7b" in source
    assert "PHASE14_V4_VISIBILITY_RECOVERY_HELPER_HEAD" in source
    assert "PHASE14_V4_VISIBILITY_RECOVERY_APPROVAL" in source
    assert "I_APPROVE_PHASE14_V4_VISIBILITY_RECOVERY" in source
    assert "production_approval_mismatch" in source
    assert '[[ "$REMOTE_MAIN" == "$HELPER_HEAD" ]]' in source
    assert source.index("production_approval_mismatch") < source.index("gcloud compute ssh")


def test_v4_visibility_recovery_preserves_research_zero_money_and_checkout() -> None:
    source = read_helper()
    for marker in (
        "require_research_zero_money",
        "require_automatic_promotion_false",
        "require_workers_four",
        '[[ "$mode" == "research" ]]',
        '[[ "$live" == "false" ]]',
        '[[ "$trade" == "0" ]]',
        '[[ "$loss" == "0" ]]',
        "LIVE_TRADING_ENABLED=false",
        "MAX_TRADE_SIZE_USD=0",
        "MAX_DAILY_LOSS_USD=0",
        "validate_deployed_checkout",
        "unexpected_deployed_head",
    ):
        assert marker in source
    assert 'git -C "$REPO" checkout' not in source
    assert 'git -C "$REPO" fetch' not in source
    assert "sed -i" not in source


def test_v4_visibility_recovery_starts_expected_chain_and_rolls_back_stopped() -> None:
    source = read_helper()
    recorder_start = source.index('systemctl start "$RECORDER_UNIT"')
    predictor_start = source.index('systemctl start "$V3_PREDICTOR"')
    execution_start = source.index('systemctl start "$V3_EXECUTION"')
    assert recorder_start < predictor_start < execution_start

    rollback = source[source.index("rollback() {") : source.index("cleanup() {")]
    assert 'systemctl stop "$V3_EXECUTION"' in rollback
    assert 'systemctl stop "$V3_PREDICTOR"' in rollback
    assert 'systemctl stop "$RECORDER_UNIT"' in rollback
    assert 'systemctl start "$MAINTENANCE_TIMER"' in rollback


def test_v4_visibility_recovery_requires_healthy_storage_and_stable_services() -> None:
    source = read_helper()
    for marker in (
        "run_storage_health",
        '"maintenance_fresh", "current_partition_present", "retention_current"',
        'require_timer_active_enabled "$MAINTENANCE_TIMER"',
        'require_timer_active_enabled "$DISK_HEALTH_TIMER"',
        'require_timer_active_enabled "$V2_TIMER"',
        'require_timer_active_enabled "$V4_TIMER"',
        'systemctl stop "$MAINTENANCE_TIMER"',
        'require_timer_enabled_inactive "$MAINTENANCE_TIMER"',
        "natural_load_soak_failed",
        "recorder_pid_changed",
        "v3_predictor_pid_changed",
        "v3_execution_pid_changed",
        "recorder_restarted_during_recovery",
        "gate_b_artifacts_changed",
        "ROLLOUT_HANDOFF_READY=true",
    ):
        assert marker in source


def test_v4_visibility_recovery_uses_portable_base64() -> None:
    source = read_helper()
    assert "base64 | tr -d '\\n'" in source
    assert "base64 -w0" not in source
