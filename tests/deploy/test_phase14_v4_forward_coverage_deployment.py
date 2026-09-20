import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "deploy" / "bp-v4-forward-coverage.service"
TIMER = ROOT / "deploy" / "bp-v4-forward-coverage.timer"
INSTALLER = ROOT / "scripts" / "deploy" / "phase14_v4_forward_coverage_install.sh"


def test_v4_forward_service_is_isolated_research_only_oneshot() -> None:
    content = SERVICE.read_text(encoding="utf-8")
    required = (
        "Type=oneshot",
        "User=bp",
        "Group=bp",
        "WorkingDirectory=/var/lib/bp/runtime/v4-forward-current",
        "EnvironmentFile=/etc/bp/bp.env",
        "EnvironmentFile=/etc/bp/bp-prospective-runtime-safety.env",
        "Environment=MODE=research",
        "Environment=LIVE_TRADING_ENABLED=false",
        "Environment=MAX_TRADE_SIZE_USD=0",
        "Environment=MAX_DAILY_LOSS_USD=0",
        "Environment=PYTHONPATH=/var/lib/bp/runtime/v4-forward-current/src",
        "/opt/bp/.venv/bin/python",
        "run_v4_forward_coverage.py once",
        "Requires=bp-postgres.service",
        "IPAddressDeny=any",
        "IPAddressAllow=localhost",
        "NoNewPrivileges=true",
        "ProtectSystem=full",
    )
    for marker in required:
        assert marker in content


def test_v4_forward_timer_runs_once_per_minute() -> None:
    content = TIMER.read_text(encoding="utf-8")
    for marker in (
        "OnBootSec=1min",
        "OnUnitActiveSec=1min",
        "Persistent=true",
        "Unit=bp-v4-forward-coverage.service",
        "WantedBy=timers.target",
    ):
        assert marker in content


def test_v4_forward_installer_does_not_deploy_or_restart_main_runtime() -> None:
    content = INSTALLER.read_text(encoding="utf-8")
    required = (
        "PHASE14_V4_FORWARD_HEAD",
        "exact 40-character verified main SHA",
        "/var/lib/bp/runtime",
        "v4-forward-current",
        "git -C \"$REPO\" archive \"$SHA\"",
        "OLD_DEPLOYED_HEAD=$(git -C \"$REPO\" rev-parse HEAD)",
        "deployed_checkout_changed",
        "bp-recorder.service",
        "bp-postgres.service",
        "research_zero_money",
        "systemctl start \"$SERVICE_UNIT\"",
        "systemctl enable --now \"$TIMER_UNIT\"",
        "future_cutoff_violation_count",
        "polymarket_predictor_key_count",
        "regime_invariant_violation_count",
        "training_run",
        "automatic_promotion",
        "recorder_restarted",
        "PHASE14_V4_FORWARD_ROLLOUT=PASS",
    )
    for marker in required:
        assert marker in content

    lowered = content.lower()
    forbidden = (
        "git -c $repo checkout",
        "git -c \"$repo\" checkout",
        "git -c $repo reset",
        "git -c \"$repo\" reset",
        "systemctl restart bp-recorder",
        "systemctl stop bp-recorder",
        "delete from market_features",
        "truncate market_features",
        "live_trading_enabled=true",
    )
    for marker in forbidden:
        assert marker not in lowered


def test_v4_forward_installer_has_clean_bash_syntax() -> None:
    result = subprocess.run(
        ["bash", "-n", str(INSTALLER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
