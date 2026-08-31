from __future__ import annotations

import importlib
from argparse import Namespace

import pytest

from bp_engine.config import Settings, TradingMode


def _module():
    return importlib.import_module("bp_engine.prospective_outcomes.cli")


def test_parser_exposes_only_money_disabled_once_and_run_commands() -> None:
    module = _module()
    parser = module.build_parser()

    once = parser.parse_args(["once", "--env-file", "/etc/bp/bp.env"])
    run = parser.parse_args(["run", "--poll-interval-seconds", "60"])
    help_text = parser.format_help().lower()

    assert once.command == "once"
    assert run.command == "run"
    assert run.poll_interval_seconds == 60.0
    for forbidden in ("wallet", "private-key", "order", "trade-size", "live-enable"):
        assert forbidden not in help_text


def test_runtime_safety_reuses_research_money_disabled_guard() -> None:
    module = _module()
    safe = Settings(
        mode=TradingMode.RESEARCH,
        live_trading_enabled=False,
        max_trade_size_usd=0,
        max_daily_loss_usd=0,
    )
    module.ensure_outcome_sync_safety(safe)

    unsafe = safe.model_copy(update={"live_trading_enabled": True})
    with pytest.raises(Exception, match="live_trading_enabled"):
        module.ensure_outcome_sync_safety(unsafe)


def test_settings_override_database_url_without_relaxing_safety(monkeypatch) -> None:
    module = _module()
    monkeypatch.setattr(
        module,
        "Settings",
        lambda **kwargs: Settings(
            mode=TradingMode.RESEARCH,
            live_trading_enabled=False,
            max_trade_size_usd=0,
            max_daily_loss_usd=0,
        ),
    )
    args = Namespace(env_file=None, database_url="sqlite+pysqlite:///:memory:")

    settings = module._settings(args)

    assert settings.database_url == "sqlite+pysqlite:///:memory:"
    module.ensure_outcome_sync_safety(settings)
