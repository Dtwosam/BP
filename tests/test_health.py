from bp_engine.config import Settings, TradingMode
from bp_engine.health import build_health_payload


def test_health_payload_reports_safe_runtime_state() -> None:
    settings = Settings(_env_file=None)

    payload = build_health_payload(settings)

    assert payload == {
        "status": "ok",
        "mode": TradingMode.RESEARCH.value,
        "live_trading_enabled": False,
        "active_horizons": ["5m", "15m"],
        "optional_horizons": ["10m"],
        "timezone": "UTC",
    }
