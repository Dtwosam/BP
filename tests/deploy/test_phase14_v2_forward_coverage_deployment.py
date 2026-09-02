from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "deploy" / "bp-v2-forward-coverage.service"
TIMER = ROOT / "deploy" / "bp-v2-forward-coverage.timer"
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


def test_ci_compiles_v2_forward_wrapper() -> None:
    content = CI.read_text(encoding="utf-8")
    assert "python -m py_compile scripts/run_v2_forward_coverage.py" in content
