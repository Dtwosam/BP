from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UNIT = (
    ROOT
    / "deploy"
    / "bp-phase15-telegram-execution-authorization-worker.service"
)
WORKER = (
    ROOT
    / "scripts"
    / "run_phase15_v3_telegram_execution_authorization_worker.py"
)


def test_execution_authorization_unit_is_offline_wallet_isolated() -> None:
    text = UNIT.read_text(encoding="utf-8")
    for marker in (
        "User=root",
        "Group=root",
        "EnvironmentFile=/etc/bp-telegram-transport/execution-auth.env",
        "ConditionPathExists=/etc/bp-telegram-transport/origin.key",
        "Environment=MODE=research",
        "Environment=LIVE_TRADING_ENABLED=false",
        "Environment=MAX_TRADE_SIZE_USD=0",
        "Environment=MAX_DAILY_LOSS_USD=0",
        (
            "UnsetEnvironment=POLYMARKET_PRIVATE_KEY POLYMARKET_WALLET_ADDRESS "
            "BP_TELEGRAM_BOT_TOKEN GOOGLE_APPLICATION_CREDENTIALS "
            "HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY "
            "http_proxy https_proxy all_proxy no_proxy"
        ),
        "NoNewPrivileges=true",
        "ProtectSystem=strict",
        "CapabilityBoundingSet=",
        "AmbientCapabilities=",
        (
            "InaccessiblePaths=/etc/bp-canary /opt/bp-canary "
            "-/etc/bp -/etc/bp-telegram-transport/transport.key"
        ),
        (
            "ReadOnlyPaths=/opt/bp-telegram-transport "
            "/etc/bp-telegram-transport/execution-auth.env "
            "/etc/bp-telegram-transport/origin.key "
            "/var/lib/bp-canary/telegram-transport-ready"
        ),
        "telegram-dispatch-claims",
        "telegram-execution-authorized",
        "telegram-execution-auth-processed",
        "telegram-execution-auth-failures",
        "RestrictAddressFamilies=AF_UNIX",
    ):
        assert marker in text

    for forbidden in (
        "AF_INET",
        "AF_INET6",
        "/etc/bp-canary/live.env",
        "/opt/bp-canary/executor.sh",
        "PHASE15_ACCEPT_REAL_MONEY",
        "POLYMARKET_PRIVATE_KEY=",
        "POLYMARKET_WALLET_ADDRESS=",
        "phase15_v3_canary_telegram_handoff",
    ):
        assert forbidden not in text


def test_execution_authorization_unit_only_executes_authorization_worker() -> None:
    text = UNIT.read_text(encoding="utf-8")
    assert str(WORKER.relative_to(ROOT)) in text
    assert "run_phase15_v3_telegram_pubsub_streaming_receive" not in text
    assert "run_phase15_v3_telegram_transport_claim_worker" not in text
    assert "phase15_v3_canary_executor" not in text
    assert "phase15_v3_canary_arm" not in text
