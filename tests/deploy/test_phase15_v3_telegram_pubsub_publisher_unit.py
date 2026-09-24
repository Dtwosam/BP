from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UNIT = ROOT / "deploy/bp-phase15-telegram-pubsub-publisher.service"
WORKER = ROOT / "scripts/run_phase15_v3_telegram_pubsub_publish_worker.py"


def test_pubsub_publisher_unit_is_zero_money_and_secret_isolated() -> None:
    text = UNIT.read_text(encoding="utf-8")
    for marker in (
        "User=bp",
        "Group=bp",
        "EnvironmentFile=/etc/bp/telegram-pubsub-publisher.env",
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
        "ReadOnlyPaths=/opt/bp-phase15-telegram-approval",
        "ReadWritePaths=/var/lib/bp/phase15-canary-telegram-transport",
    ):
        assert marker in text

    for forbidden in (
        "/etc/bp-canary/live.env",
        "/opt/bp-canary/executor.sh",
        "PHASE15_ACCEPT_REAL_MONEY",
        "POLYMARKET_PRIVATE_KEY=",
        "POLYMARKET_WALLET_ADDRESS=",
    ):
        assert forbidden not in text


def test_pubsub_publisher_unit_only_executes_transport_worker() -> None:
    text = UNIT.read_text(encoding="utf-8")
    assert str(WORKER.relative_to(ROOT)) in text
    assert "run_phase15_v3_canary_telegram_handoff" not in text
    assert "phase15_v3_canary_executor" not in text
    assert "phase15_v3_canary_arm" not in text
