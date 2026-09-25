from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UNIT = ROOT / "deploy/bp-phase15-telegram-pubsub-streaming-receiver.service"
STREAMING = ROOT / "scripts/run_phase15_v3_telegram_pubsub_streaming_receive.py"


def test_streaming_receiver_unit_is_unprivileged_zero_money_and_wallet_isolated() -> None:
    text = UNIT.read_text(encoding="utf-8")
    for marker in (
        "User=bp-transport",
        "Group=bp-transport",
        "EnvironmentFile=/etc/bp-telegram-transport/receiver.env",
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
        "InaccessiblePaths=/etc/bp-canary -/etc/bp-telegram-transport/origin.key",
        "ReadOnlyPaths=/opt/bp-telegram-transport /etc/bp-telegram-transport/receiver.env /etc/bp-telegram-transport/transport.key",
        (
            "ReadWritePaths=/var/lib/bp-canary/telegram-transport-inbox "
            "/var/lib/bp-canary/telegram-transport-rejections"
        ),
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


def test_streaming_receiver_unit_only_executes_transport_receiver() -> None:
    text = UNIT.read_text(encoding="utf-8")
    assert str(STREAMING.relative_to(ROOT)) in text
    assert "run_phase15_v3_canary_telegram_handoff" not in text
    assert "phase15_v3_canary_executor" not in text
    assert "phase15_v3_canary_arm" not in text
