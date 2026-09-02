import importlib
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine

from bp_engine.config import Settings, TradingMode
from bp_engine.storage import schema

v2_forward_cli = importlib.import_module("bp_engine.features.v2_forward_cli")

CYCLE_AT = datetime(2026, 9, 2, 13, 0, 0, tzinfo=UTC)


def test_parser_exposes_only_one_bounded_cycle_command() -> None:
    parser = v2_forward_cli.build_parser()
    args = parser.parse_args(
        [
            "once",
            "--env-file",
            "/tmp/bp.env",
            "--database-url",
            "sqlite:///tmp.db",
            "--cycle-at",
            "2026-09-02T13:00:00Z",
        ]
    )
    assert args.command == "once"
    assert args.env_file == "/tmp/bp.env"
    assert args.database_url == "sqlite:///tmp.db"
    assert args.cycle_at == CYCLE_AT

    with pytest.raises(SystemExit):
        parser.parse_args(["run"])


@pytest.mark.parametrize(
    ("changes",),
    [
        ({"mode": TradingMode.PAPER},),
        ({"mode": TradingMode.LIVE},),
        ({"live_trading_enabled": True},),
        ({"max_trade_size_usd": 1},),
        ({"max_daily_loss_usd": 1},),
    ],
)
def test_research_zero_money_guard_rejects_unsafe_settings(changes) -> None:
    settings = Settings(_env_file=None).model_copy(update=changes)
    with pytest.raises(
        ValueError,
        match="V2 forward coverage requires RESEARCH/live-disabled/zero-money safety",
    ):
        v2_forward_cli.require_research_zero_money(settings)


def test_safe_settings_pass_research_zero_money_guard() -> None:
    v2_forward_cli.require_research_zero_money(Settings(_env_file=None))


def test_safety_is_checked_before_database_engine_creation(monkeypatch) -> None:
    unsafe = Settings(_env_file=None).model_copy(update={"live_trading_enabled": True})
    parser = v2_forward_cli.build_parser()
    args = parser.parse_args(["once", "--cycle-at", "2026-09-02T13:00:00Z"])
    monkeypatch.setattr(v2_forward_cli, "_settings", lambda _args: unsafe)

    def _unexpected_engine(_url):
        raise AssertionError("database engine created before safety guard")

    monkeypatch.setattr(v2_forward_cli, "create_engine", _unexpected_engine)
    with pytest.raises(
        ValueError,
        match="V2 forward coverage requires RESEARCH/live-disabled/zero-money safety",
    ):
        v2_forward_cli._run(args)


def test_once_emits_deterministic_cycle_json(tmp_path, capsys) -> None:
    database = tmp_path / "forward.db"
    url = f"sqlite:///{database}"
    engine = create_engine(url)
    schema.metadata.create_all(engine)
    engine.dispose()

    assert (
        v2_forward_cli.main(
            [
                "once",
                "--database-url",
                url,
                "--cycle-at",
                "2026-09-02T13:00:00Z",
            ]
        )
        == 0
    )
    first = json.loads(capsys.readouterr().out)

    assert (
        v2_forward_cli.main(
            [
                "once",
                "--database-url",
                url,
                "--cycle-at",
                "2026-09-02T13:00:00Z",
            ]
        )
        == 0
    )
    second = json.loads(capsys.readouterr().out)

    expected = {
        "automatic_promotion": False,
        "coverage_market_count": 0,
        "coverage_row_count": 0,
        "cycle_at": "2026-09-02T13:00:00+00:00",
        "eligible_targets": 0,
        "existing": 0,
        "future_cutoff_violation_count": 0,
        "inserted": 0,
        "planned_rows": 0,
        "policy_selected": False,
    }
    assert first == expected
    assert second == expected


def test_recurring_collector_does_not_bootstrap_schema() -> None:
    source = importlib.import_module("inspect").getsource(v2_forward_cli)
    assert "metadata.create_all" not in source
