from pathlib import Path

from bp_engine.config import Settings, TradingMode, get_settings


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
    settings = Settings(_env_file=None)

    assert settings.recorder_queue_maxsize > 0
    assert settings.recorder_batch_size > 0
    assert settings.recorder_flush_interval_seconds > 0
    assert settings.polymarket_refresh_interval_seconds > 0
    assert settings.database_url.startswith("postgresql+psycopg://")
    assert settings.live_trading_enabled is False


def test_storage_defaults_bound_raw_data_and_protect_disk() -> None:
    settings = Settings(_env_file=None)

    assert settings.storage_hot_raw_hours == 24
    assert settings.storage_archive_retention_hours == 24
    assert settings.storage_state_retention_days == 90
    assert settings.storage_archive_dir == "/var/lib/bp/archive/raw"
    assert settings.storage_warning_free_gib == 25
    assert settings.storage_critical_free_gib == 15
    assert settings.storage_delete_batch_size == 50_000
    assert settings.storage_warning_free_gib > settings.storage_critical_free_gib


def test_storage_settings_accept_environment_overrides(monkeypatch) -> None:
    monkeypatch.setenv("STORAGE_HOT_RAW_HOURS", "36")
    monkeypatch.setenv("STORAGE_ARCHIVE_RETENTION_HOURS", "12")
    monkeypatch.setenv("STORAGE_STATE_RETENTION_DAYS", "60")
    monkeypatch.setenv("STORAGE_ARCHIVE_DIR", "/tmp/bp-archive")
    monkeypatch.setenv("STORAGE_WARNING_FREE_GIB", "30")
    monkeypatch.setenv("STORAGE_CRITICAL_FREE_GIB", "20")
    monkeypatch.setenv("STORAGE_DELETE_BATCH_SIZE", "1234")

    settings = Settings(_env_file=None)

    assert settings.storage_hot_raw_hours == 36
    assert settings.storage_archive_retention_hours == 12
    assert settings.storage_state_retention_days == 60
    assert settings.storage_archive_dir == "/tmp/bp-archive"
    assert settings.storage_warning_free_gib == 30
    assert settings.storage_critical_free_gib == 20
    assert settings.storage_delete_batch_size == 1234


def test_get_settings_honors_bp_env_file(monkeypatch, tmp_path: Path) -> None:
    env_file = tmp_path / "bp.env"
    env_file.write_text(
        "MODE=research\n"
        "LIVE_TRADING_ENABLED=false\n"
        "MAX_TRADE_SIZE_USD=0\n"
        "MAX_DAILY_LOSS_USD=0\n"
        "DATABASE_URL=postgresql+psycopg://bp:secret-not-in-argv@127.0.0.1:5432/bp\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("BP_ENV_FILE", str(env_file))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.mode is TradingMode.RESEARCH
        assert settings.live_trading_enabled is False
        assert settings.max_trade_size_usd == 0
        assert settings.max_daily_loss_usd == 0
        assert settings.database_url.endswith("secret-not-in-argv@127.0.0.1:5432/bp")
    finally:
        get_settings.cache_clear()
