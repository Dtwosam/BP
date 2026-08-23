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


def test_recorder_defaults_are_bounded_and_keep_trading_disabled() -> None:
    settings = Settings()

    assert settings.recorder_queue_maxsize > 0
    assert settings.recorder_batch_size > 0
    assert settings.recorder_flush_interval_seconds > 0
    assert settings.polymarket_refresh_interval_seconds > 0
    assert settings.database_url.startswith("postgresql+psycopg://")
    assert settings.live_trading_enabled is False
