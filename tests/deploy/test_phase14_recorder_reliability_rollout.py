from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts/deploy/phase14_recorder_reliability_rollout_cloudshell.sh"
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


def test_recorder_reliability_rollout_is_exact_head_guarded_and_rollback_capable() -> None:
    assert HELPER.is_file(), HELPER
    content = HELPER.read_text(encoding="utf-8")

    assert "set -Eeuo pipefail" in content
    assert "PHASE14_RECORDER_RELIABILITY_HEAD" in content
    assert "PHASE14_RECORDER_RELIABILITY_BRANCH" in content
    assert "^[0-9a-f]{40}$" in content
    assert "gcloud auth list" in content
    assert "git -C /opt/bp fetch --no-tags origin" in content
    assert "refs/remotes/origin/$BRANCH" in content
    assert '"$FETCHED" != "$SHA"' in content

    assert "validate_deployed_checkout" in content
    assert "unexpected_deployed_checkout_change" in content
    for tracked in (
        "apps/dashboard/next-env.d.ts",
        "apps/dashboard/tsconfig.json",
    ):
        assert tracked in content
    for runtime_path in (
        ".node/",
        "apps/dashboard/.next/",
        "apps/dashboard/node_modules/",
        "apps/dashboard/tsconfig.tsbuildinfo",
    ):
        assert runtime_path in content

    assert "validate_rollout_scope" in content
    assert "unexpected_rollout_path" in content
    assert "src/bp_engine/collectors/reliability.py" in content
    assert "src/bp_engine/collectors/websocket_runner.py" in content

    assert "storage_maintenance.py disk-health" in content
    for key in (
        "MODE",
        "LIVE_TRADING_ENABLED",
        "MAX_TRADE_SIZE_USD",
        "MAX_DAILY_LOSS_USD",
    ):
        assert key in content
    assert '"$MODE" != "research"' in content
    assert '"$LIVE_TRADING_ENABLED" != "false"' in content
    assert '"$MAX_TRADE_SIZE_USD" != "0"' in content
    assert '"$MAX_DAILY_LOSS_USD" != "0"' in content

    for service in CORE_SERVICES:
        assert service in content

    assert "phase14_prospective_runtime_install.sh" in content
    assert 'systemctl restart "$RECORDER_UNIT"' in content
    assert "sleep 45" in content
    assert "scripts/soak_report.py" in content
    assert "--hours 0.01" in content
    assert "--minimum-hours 0.008" in content

    assert "http://127.0.0.1:8787/api/v1/snapshot" in content
    assert 'mode.get("trading_mode") != "RESEARCH"' in content
    assert 'mode.get("live_trading_enabled") is not False' in content
    assert 'mode.get("execution_available") is not False' in content
    assert 'mode.get("paper_execution_available") is not True' in content

    assert "OLD_HEAD" in content
    assert "ROLLBACK_ARMED" in content
    assert "rollback_recorder_reliability_rollout" in content
    assert "trap cleanup EXIT" in content
    assert "/var/lib/bp/evidence/phase14-recorder-reliability-rollout-" in content
    assert "PHASE14_RECORDER_RELIABILITY_ROLLOUT=PASS" in content

    lowered = content.lower()
    for forbidden in (
        "wallet",
        "private_key",
        "private-key",
        "place_order",
        "submit_order",
        "live_trading_enabled=true",
    ):
        assert forbidden not in lowered

    for service in (
        "bp-postgres.service",
        "bp-dashboard-api.service",
        "bp-dashboard-web.service",
        "bp-paper-execution.service",
    ):
        assert f"systemctl restart {service}" not in content
        assert f'systemctl restart "{service}"' not in content
        assert f"systemctl stop {service}" not in content
        assert f'systemctl stop "{service}"' not in content

    ci = CI.read_text(encoding="utf-8")
    assert "bash -n scripts/deploy/phase14_recorder_reliability_rollout_cloudshell.sh" in ci
