from pathlib import Path

import pytest
from bp_engine.dashboard.__main__ import DASHBOARD_API_HOST, validate_dashboard_safety

from bp_engine.config import Settings, TradingMode


def test_dashboard_defaults_to_loopback_and_port_8787() -> None:
    settings = Settings(_env_file=None)

    assert DASHBOARD_API_HOST == "127.0.0.1"
    assert settings.dashboard_api_port == 8787


@pytest.mark.parametrize(
    "override",
    [
        {"mode": TradingMode.PAPER},
        {"mode": TradingMode.LIVE},
        {"live_trading_enabled": True},
        {"max_trade_size_usd": 1},
        {"max_daily_loss_usd": 1},
    ],
)
def test_dashboard_startup_rejects_non_research_safety_state(override: dict[str, object]) -> None:
    settings = Settings(_env_file=None, **override)

    with pytest.raises(RuntimeError, match="dashboard safety interlock"):
        validate_dashboard_safety(settings)


def test_dashboard_startup_accepts_research_live_off_zero_limits() -> None:
    settings = Settings(_env_file=None)

    validate_dashboard_safety(settings)


def test_dashboard_package_contains_no_trading_auth_imports() -> None:
    dashboard_root = Path("src/bp_engine/dashboard")
    forbidden = ("private_key", "wallet", "allowance", "place_order", "signing")
    text = "\n".join(path.read_text() for path in dashboard_root.glob("*.py"))

    assert not any(term in text.lower() for term in forbidden)
