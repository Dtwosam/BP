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


def test_project_state_invalidates_prior_sha_bound_recovery_and_rollout_authorization() -> None:
    import json

    state = json.loads((ROOT / "PROJECT_STATE.json").read_text(encoding="utf-8"))
    assert state["source_of_truth_version"] == "0.14.178"
    storage = state["phase_14_storage_reliability_followup"]
    assert storage["concurrent_partition_retirement_production_rollout_authorized"] is False
    assert (
        storage["concurrent_partition_retirement_rollout_gate_production_authorized"]
        is False
    )
    assert storage["recorder_v3_recovery_authorized"] is False
    assert storage["recorder_v3_recovery_performed"] is True
    assert storage["recorder_v3_recovery_last_attempt_status"] == "PASS"
    assert storage["recorder_v3_recovery_gate_repository_status"] == (
        "PRODUCTION_PASS_ROLLOUT_PASS_RUNTIME_ACTIVE"
    )
    assert storage["recorder_v3_current_runtime_status"] == (
        "ACTIVE_AFTER_CONCURRENT_PARTITION_RETIREMENT_ROLLOUT_PASS"
    )
    assert storage["recorder_v3_current_runtime_recorder_active"] is True
    assert storage["recorder_v3_current_runtime_v3_predictor_active"] is True
    assert storage["recorder_v3_current_runtime_v3_execution_active"] is True
    assert storage["recorder_v3_current_runtime_maintenance_timer_active"] is True
    assert storage["recorder_v3_recovery_production_pass_rollout_handoff_ready"] is True
    assert storage["recorder_v3_recovery_production_pass_maintenance_timer_active"] is False
    assert storage["recorder_v3_recovery_production_pass_recorder_pid"] == 4016465
    assert storage["recorder_v3_recovery_production_pass_predictor_pid"] == 4016471
    assert storage["recorder_v3_recovery_production_pass_execution_pid"] == 4016476
    assert (
        storage[
            "prior_recovery_rollout_sha_bound_authorization_invalidated_by_main_advance"
        ]
        is True
    )
    assert storage["compact_feed_freshness_index_production_authorized"] is False
    assert storage["compact_feed_freshness_index_production_performed"] is True
    assert (
        storage["recorder_v3_recovery_gate_helper"]
        == "scripts/deploy/phase14_recorder_v3_recovery_gate_cloudshell.sh"
    )
    assert (
        storage["recorder_v3_recovery_expected_production_head"]
        == "7c3af78da1922a0e5187c24b799951130cc98887"
    )


def test_recorder_v3_recovery_waits_for_inflight_maintenance_and_stops_timer_for_handoff() -> None:
    source = read_helper()
    assert "wait_for_oneshot_idle_success" in source
    assert 'wait_for_oneshot_idle_success "$MAINTENANCE_SERVICE" 3600' in source
    assert 'wait_for_oneshot_idle_success "$DISK_HEALTH_SERVICE" 30' in source
    assert "oneshot_wait_timeout" in source
    assert "oneshot_last_result_not_success" in source
    assert 'require_timer_headroom "$MAINTENANCE_TIMER" 600' in source
    assert "timer_headroom_insufficient" in source
    timer_stop = source.index('systemctl stop "$MAINTENANCE_TIMER"')
    recorder_start = source.index('systemctl start "$RECORDER_UNIT"')
    assert timer_stop < recorder_start
    assert 'require_timer_enabled_inactive "$MAINTENANCE_TIMER"' in source
    rollback = source[source.index("rollback() {") : source.index("cleanup() {")]
    assert 'systemctl start "$MAINTENANCE_TIMER"' in rollback
    assert "MAINTENANCE_TIMER_ACTIVE=inactive" in source
    assert "ROLLOUT_HANDOFF_READY=true" in source


def test_recorder_v3_recovery_uses_canonical_soak_report_schema() -> None:
    source = read_helper()
    assert 'payload.get("passed") is not True' in source
    assert 'payload.get("verdict")' not in source
    for marker in (
        '"polymarket/market"',
        '"bybit/spot"',
        '"bybit/linear"',
        '"coinbase/spot"',
        '"event_count"',
        '"backpressure"',
        "required feeds missing events",
        "backpressure recorded for",
    ):
        assert marker in source


def test_recorder_v3_recovery_matches_ci_short_soak_warmup() -> None:
    source = read_helper()
    warmup_at = source.index("sleep 45")
    soak_at = source.index("run_soak", warmup_at)
    assert warmup_at < soak_at
    assert "sleep 20\nrun_soak" not in source
    ci = (ROOT / ".github" / "workflows" / "recorder-short-soak.yml").read_text(
        encoding="utf-8"
    )
    assert "for _ in $(seq 1 45)" in ci
    assert "python scripts/soak_report.py --hours 0.01 --minimum-hours 0.008" in ci


def test_recorder_v3_recovery_uses_canonical_dashboard_safety_schema() -> None:
    source = read_helper()
    assert 'mode.get("trading_mode") != "RESEARCH"' in source
    assert 'mode.get("live_trading_enabled") is not False' in source
    assert 'mode.get("execution_available") is not False' in source
    assert 'mode.get("mode")' not in source
    assert "dashboard max trade size nonzero" not in source
    assert "dashboard max daily loss nonzero" not in source
    rollout = (
        ROOT
        / "scripts"
        / "deploy"
        / "phase14_concurrent_partition_retirement_rollout_cloudshell.sh"
    ).read_text(encoding="utf-8")
    for marker in (
        'mode.get("trading_mode") != "RESEARCH"',
        'mode.get("live_trading_enabled") is not False',
        'mode.get("execution_available") is not False',
    ):
        assert marker in rollout
