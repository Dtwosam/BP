from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "deploy" / "bp-v2-forward-coverage.service"
TIMER = ROOT / "deploy" / "bp-v2-forward-coverage.timer"
HELPER = ROOT / "scripts" / "deploy" / "phase14_v2_forward_coverage_rollout_cloudshell.sh"
CI = ROOT / ".github" / "workflows" / "ci.yml"


def test_v2_forward_service_is_hardened_research_only_oneshot() -> None:
    assert SERVICE.is_file(), SERVICE
    content = SERVICE.read_text(encoding="utf-8")
    exec_start = (
        "ExecStart=/opt/bp/.venv/bin/python "
        "/opt/bp/scripts/run_v2_forward_coverage.py once --env-file /etc/bp/bp.env"
    )
    required = (
        "Type=oneshot",
        "User=bp",
        "Group=bp",
        "WorkingDirectory=/opt/bp",
        "EnvironmentFile=/etc/bp/bp.env",
        "EnvironmentFile=/etc/bp/bp-prospective-runtime-safety.env",
        "Environment=MODE=research",
        "Environment=LIVE_TRADING_ENABLED=false",
        "Environment=MAX_TRADE_SIZE_USD=0",
        "Environment=MAX_DAILY_LOSS_USD=0",
        exec_start,
        "Requires=bp-postgres.service",
        "After=bp-postgres.service",
        "UMask=0077",
        "TimeoutStartSec=2min",
        "NoNewPrivileges=true",
        "PrivateTmp=true",
        "PrivateDevices=true",
        "ProtectHome=true",
        "ProtectSystem=full",
        "RestrictAddressFamilies=AF_UNIX",
        "StandardOutput=journal",
        "StandardError=journal",
        "SyslogIdentifier=bp-v2-forward-coverage",
    )
    for line in required:
        assert line in content
    for forbidden in ("wallet", "private_key", "signing", "order", "LIVE_TRADING_ENABLED=true"):
        assert forbidden not in content


def test_v2_forward_timer_is_persistent_one_minute_schedule() -> None:
    assert TIMER.is_file(), TIMER
    content = TIMER.read_text(encoding="utf-8")
    for line in (
        "OnBootSec=1min",
        "OnUnitActiveSec=1min",
        "Persistent=true",
        "Unit=bp-v2-forward-coverage.service",
        "WantedBy=timers.target",
    ):
        assert line in content


def test_v2_forward_rollout_helper_is_exact_head_guarded_and_rollback_capable() -> None:
    assert HELPER.is_file(), HELPER
    content = HELPER.read_text(encoding="utf-8")
    required = (
        "PHASE14_V2_FORWARD_HEAD",
        "PHASE14_V2_FORWARD_FROM_HEAD",
        "PHASE14_V2_FORWARD_PROJECT",
        "PHASE14_V2_FORWARD_ZONE",
        "PHASE14_V2_FORWARD_VM",
        "PHASE14_V2_FORWARD_BRANCH",
        "PHASE14_V2_FORWARD_ENV_FILE",
        "exact 40-character verified candidate SHA",
        "no active gcloud account",
        'OLD_HEAD=$(git -C "$REPO" rev-parse HEAD)',
        '[[ "$OLD_HEAD" == "$EXPECTED_FROM_HEAD" ]]',
        'REMOTE_HEAD=$(git -C "$REPO" rev-parse "origin/$BRANCH")',
        '[[ "$REMOTE_HEAD" == "$SHA" ]]',
        'git -C "$REPO" merge-base --is-ancestor "$OLD_HEAD" "$SHA"',
        "unexpected_rollout_path",
        "frozen_v1_path_changed",
        "src/bp_engine/features/service.py",
        "src/bp_engine/live_prediction",
        "src/bp_engine/calibration",
        "src/bp_engine/execution",
        "research_zero_money_boundary_not_satisfied",
        "bp-recorder.service",
        "bp-postgres.service",
        "bp-dashboard-api.service",
        "bp-dashboard-web.service",
        "bp-paper-execution.service",
        "bp-live-predictor.service",
        "bp-prospective-outcomes.service",
        "storage_maintenance.py",
        "disk-health",
        "bp-v2-forward-coverage.service",
        "bp-v2-forward-coverage.timer",
        "systemctl enable --now \"$TIMER_UNIT\"",
        "systemctl start \"$SERVICE_UNIT\"",
        "future_cutoff_violation_count",
        "policy_selected",
        "automatic_promotion",
        "/var/lib/bp/evidence/",
        "ROLLBACK_ARMED=1",
        "rollback()",
        "PHASE14_V2_FORWARD_ROLLOUT=PASS",
    )
    for marker in required:
        assert marker in content

    lowered = content.lower()
    for forbidden in (
        "delete from market_features",
        "truncate market_features",
        "live_trading_enabled=true",
        "max_trade_size_usd=1",
        "max_daily_loss_usd=1",
    ):
        assert forbidden not in lowered


def test_v2_forward_rollout_scope_is_narrow() -> None:
    content = HELPER.read_text(encoding="utf-8")
    for allowed in (
        "PROJECT_STATE.json",
        "docs/*",
        ".github/workflows/ci.yml",
        "src/bp_engine/features/v2_forward.py",
        "src/bp_engine/features/v2_forward_cli.py",
        "scripts/run_v2_forward_coverage.py",
        "deploy/bp-v2-forward-coverage.service",
        "deploy/bp-v2-forward-coverage.timer",
        "scripts/deploy/phase14_v2_forward_coverage_rollout_cloudshell.sh",
        "tests/features/test_v2_forward.py",
        "tests/features/test_v2_forward_cli.py",
        "tests/deploy/test_phase14_v2_forward_coverage_deployment.py",
    ):
        assert allowed in content


def test_ci_validates_v2_forward_runtime_assets() -> None:
    content = CI.read_text(encoding="utf-8")
    assert "python -m py_compile scripts/run_v2_forward_coverage.py" in content
    assert "bash -n scripts/deploy/phase14_v2_forward_coverage_rollout_cloudshell.sh" in content
