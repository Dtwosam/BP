from bp_engine.config import Settings


def test_live_readiness_defaults_are_fail_closed() -> None:
    settings = Settings(_env_file=None)

    assert settings.live_trading_enabled is False
    assert settings.max_trade_size_usd == 0
    assert settings.max_daily_loss_usd == 0
    assert settings.max_total_exposure_usd == 0
    assert settings.max_consecutive_losses == 0
    assert settings.live_min_edge == 0
    assert settings.live_min_probability == 0
    assert settings.live_min_liquidity_usd == 0
    assert settings.live_max_spread == 0
    assert settings.live_max_prediction_age_seconds == 0
    assert settings.live_min_time_to_expiry_seconds == 0
    assert settings.live_cooldown_seconds == 0
    assert settings.live_activation_manifest_path == "/var/lib/bp/live/activation.json"
    assert settings.live_kill_switch_path == "/var/lib/bp/live/KILL"
    assert settings.polymarket_geoblock_url == "https://polymarket.com/api/geoblock"
    assert settings.polymarket_private_key_env == "POLYMARKET_PRIVATE_KEY"
    assert settings.polymarket_wallet_address_env == "POLYMARKET_WALLET_ADDRESS"


def test_live_readiness_env_overrides_parse_without_secret_values(monkeypatch) -> None:
    monkeypatch.setenv("MAX_TOTAL_EXPOSURE_USD", "25")
    monkeypatch.setenv("MAX_CONSECUTIVE_LOSSES", "3")
    monkeypatch.setenv("LIVE_MAX_SPREAD", "0.04")
    monkeypatch.setenv("LIVE_MIN_EDGE", "0.03")

    settings = Settings(_env_file=None)

    assert settings.max_total_exposure_usd == 25
    assert settings.max_consecutive_losses == 3
    assert settings.live_max_spread == 0.04
    assert settings.live_min_edge == 0.03
