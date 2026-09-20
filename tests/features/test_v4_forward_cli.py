import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine

from bp_engine.config import Settings, TradingMode
from bp_engine.features import v4_forward_cli
from bp_engine.storage import schema

CYCLE_AT = datetime(2026, 9, 20, 13, 30, tzinfo=UTC)


def test_parser_exposes_only_one_bounded_cycle_command() -> None:
    parser = v4_forward_cli.build_parser()
    args = parser.parse_args(
        [
            "once",
            "--env-file",
            "/tmp/bp.env",
            "--database-url",
            "sqlite:///tmp.db",
            "--cycle-at",
            "2026-09-20T13:30:00Z",
        ]
    )
    assert args.command == "once"
    assert args.cycle_at == CYCLE_AT

    with pytest.raises(SystemExit):
        parser.parse_args(["run"])


@pytest.mark.parametrize(
    "changes",
    [
        {"mode": TradingMode.PAPER},
        {"mode": TradingMode.LIVE},
        {"live_trading_enabled": True},
        {"max_trade_size_usd": 1},
        {"max_daily_loss_usd": 1},
    ],
)
def test_research_zero_money_guard_rejects_unsafe_settings(changes) -> None:
    settings = Settings(_env_file=None).model_copy(update=changes)
    with pytest.raises(
        ValueError,
        match="V4 forward coverage requires RESEARCH/live-disabled/zero-money safety",
    ):
        v4_forward_cli.require_research_zero_money(settings)


def test_safety_is_checked_before_database_engine_creation(monkeypatch) -> None:
    unsafe = Settings(_env_file=None).model_copy(update={"live_trading_enabled": True})
    parser = v4_forward_cli.build_parser()
    args = parser.parse_args(["once", "--cycle-at", "2026-09-20T13:30:00Z"])
    monkeypatch.setattr(v4_forward_cli, "_settings", lambda _args: unsafe)

    def unexpected_engine(_url):
        raise AssertionError("database engine created before safety guard")

    monkeypatch.setattr(v4_forward_cli, "create_engine", unexpected_engine)
    with pytest.raises(ValueError, match="V4 forward coverage requires"):
        v4_forward_cli._run(args)


def test_empty_once_cycle_emits_deterministic_safe_json(tmp_path, capsys) -> None:
    database = tmp_path / "v4-forward.db"
    url = f"sqlite:///{database}"
    engine = create_engine(url)
    schema.metadata.create_all(engine)
    engine.dispose()

    assert v4_forward_cli.main(
        [
            "once",
            "--database-url",
            url,
            "--cycle-at",
            "2026-09-20T13:30:00Z",
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["cycle_at"] == "2026-09-20T13:30:00+00:00"
    assert payload["epoch"] == "2026-09-20T12:40:53+00:00"
    assert payload["eligible_targets"] == 0
    assert payload["inserted"] == 0
    assert payload["coverage_market_count"] == 0
    assert payload["future_cutoff_violation_count"] == 0
    assert payload["polymarket_predictor_key_count"] == 0
    assert payload["policy_selected"] is False
    assert payload["training_run"] is False
    assert payload["automatic_promotion"] is False


def test_recurring_collector_does_not_bootstrap_schema() -> None:
    source = __import__("inspect").getsource(v4_forward_cli)
    assert "metadata.create_all" not in source
