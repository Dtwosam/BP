from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts/deploy/phase14_v2_gate_a_rollout_cloudshell.sh"
CI = ROOT / ".github/workflows/ci.yml"

CORE_SERVICES = (
    "bp-recorder.service",
    "bp-postgres.service",
    "bp-dashboard-api.service",
    "bp-dashboard-web.service",
    "bp-paper-execution.service",
    "bp-live-predictor.service",
    "bp-prospective-outcomes.service",
)


def test_v2_gate_a_rollout_is_exact_head_guarded_and_research_only() -> None:
    assert HELPER.is_file(), HELPER
    content = HELPER.read_text(encoding="utf-8")

    assert "set -Eeuo pipefail" in content
    assert "PHASE14_V2_GATE_A_HEAD" in content
    assert "PHASE14_V2_GATE_A_FROM_HEAD" in content
    assert "PHASE14_V2_GATE_A_BRANCH" in content
    assert "^[0-9a-f]{40}$" in content
    assert "gcloud auth list" in content
    assert "git -C /opt/bp fetch --no-tags origin" in content
    assert "refs/remotes/origin/$BRANCH" in content
    assert '"$FETCHED" != "$SHA"' in content
    assert "candidate_not_descendant_of_deployed_head" in content

    assert "validate_deployed_checkout" in content
    assert "validate_rollout_scope" in content
    assert "unexpected_rollout_path" in content
    for required_path in (
        "src/bp_engine/recorder/state.py",
        "src/bp_engine/features/v2_",
        "scripts/generate_v2_features.py",
        "scripts/report_v2_feature_coverage.py",
    ):
        assert required_path in content

    for frozen_path in (
        "src/bp_engine/features/service.py",
        "src/bp_engine/live_prediction",
        "src/bp_engine/calibration",
        "src/bp_engine/execution",
    ):
        assert frozen_path in content
    assert "frozen_v1_path_changed" in content

    assert "require_research_zero_money" in content
    assert "research_zero_money_boundary_not_satisfied" in content
    for expected in (
        '"$mode" != "research"',
        '"$live_trading_enabled" != "false"',
        '"$max_trade_size_usd" != "0"',
        '"$max_daily_loss_usd" != "0"',
    ):
        assert expected in content

    for service in CORE_SERVICES:
        assert service in content

    assert "phase14_prospective_runtime_install.sh" in content
    assert 'systemctl restart "$RECORDER_UNIT"' in content
    for service in CORE_SERVICES:
        if service == "bp-recorder.service":
            continue
        assert f"systemctl restart {service}" not in content
        assert f'systemctl restart "{service}"' not in content

    assert "prove_real_last_trade_provenance" in content
    for key in (
        "last_trade_price",
        "last_trade_source_at",
        "last_trade_received_at",
        "last_trade_event_dedupe_key",
    ):
        assert key in content
    assert "generic_activity_preserved_trade_timestamp" in content

    assert "wait_for_forward_5m_market" in content
    assert "generate_v2_features.py" in content
    assert "--preserve-existing" in content
    assert 'TARGET_START=' in content
    assert 'TARGET_END=' in content
    assert '--start "$TARGET_START"' in content
    assert '--end "$TARGET_END"' in content
    assert '--start "$ROLLOUT_STARTED"' not in content
    assert "core-v2-last-trade" in content
    for offset in (60, 120, 180, 240):
        assert str(offset) in content
    assert "future_source_cutoff" in content

    assert "report_v2_feature_coverage.py" in content
    assert 'report.get("policy_selected") is not False' in content
    assert 'report.get("automatic_promotion") is not False' in content
    assert 'report.get("future_cutoff_violation_count") != 0' in content

    assert "storage_maintenance.py" in content
    assert "disk-health" in content
    assert "http://127.0.0.1:8787/api/v1/snapshot" in content
    assert 'mode.get("trading_mode") != "RESEARCH"' in content
    assert 'mode.get("live_trading_enabled") is not False' in content
    assert 'mode.get("execution_available") is not False' in content

    assert "ROLLBACK_ARMED" in content
    assert "rollback_v2_gate_a_rollout" in content
    assert "trap cleanup EXIT" in content
    assert "/var/lib/bp/evidence/phase14-v2-gate-a-rollout-" in content
    assert "PHASE14_V2_GATE_A_ROLLOUT=PASS" in content

    lowered = content.lower()
    for forbidden in (
        "private_key",
        "private-key",
        "place_order",
        "submit_order",
        "live_trading_enabled=true",
        "max_trade_size_usd=1",
        "max_daily_loss_usd=1",
    ):
        assert forbidden not in lowered


def test_v2_gate_a_rollout_has_ci_shell_syntax_gate() -> None:
    ci = CI.read_text(encoding="utf-8")
    assert "bash -n scripts/deploy/phase14_v2_gate_a_rollout_cloudshell.sh" in ci
