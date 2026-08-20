from bp_engine.config import Settings, TradingMode


def test_settings_use_safe_project_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.mode is TradingMode.RESEARCH
    assert settings.live_trading_enabled is False
    assert settings.active_horizons == ("5m", "15m")
    assert settings.optional_horizons == ("10m",)
    assert settings.timezone == "UTC"
    assert settings.max_trade_size_usd == 0
    assert settings.max_daily_loss_usd == 0
