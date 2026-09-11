from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from bp_engine.v2_research import cli

PLANNING_EPOCH = datetime(2026, 9, 11, 12, 48, 1, tzinfo=UTC)


def test_readiness_and_plan_parse_timezone_aware_planning_epoch() -> None:
    parser = cli.build_parser()

    readiness = parser.parse_args(
        ["readiness", "--planning-epoch-start", "2026-09-11T12:48:01Z"]
    )
    plan = parser.parse_args(
        [
            "plan",
            "--output",
            "plan.json",
            "--planning-epoch-start",
            "2026-09-11T12:48:01Z",
        ]
    )

    assert readiness.planning_epoch_start == PLANNING_EPOCH
    assert plan.planning_epoch_start == PLANNING_EPOCH


def test_planning_epoch_cli_rejects_naive_timestamp() -> None:
    parser = cli.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(
            ["readiness", "--planning-epoch-start", "2026-09-11T12:48:01"]
        )


def test_cli_forwards_planning_epoch_to_feature_only_readiness_and_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser = cli.build_parser()
    captured: list[tuple[str, datetime | None]] = []

    monkeypatch.setattr(
        cli,
        "_settings",
        lambda args: SimpleNamespace(database_url="sqlite+pysqlite:///:memory:"),
    )
    monkeypatch.setattr(cli, "create_engine", lambda url: object())
    monkeypatch.setattr(
        cli,
        "_read_only",
        lambda engine, operation: operation(object()),
    )
    monkeypatch.setattr(cli, "_write_exclusive", lambda path, payload: None)

    def fake_readiness(
        connection,
        config,
        research_config,
        *,
        planning_epoch_start=None,
    ):
        captured.append(("readiness", planning_epoch_start))
        return {"ready": False}

    def fake_plan(
        connection,
        config,
        research_config,
        *,
        planning_epoch_start=None,
    ):
        captured.append(("plan", planning_epoch_start))
        return {"plan_sha256": "a" * 64}

    monkeypatch.setattr(cli, "assess_gate_b_readiness", fake_readiness)
    monkeypatch.setattr(cli, "build_gate_b_plan", fake_plan)

    readiness_args = parser.parse_args(
        ["readiness", "--planning-epoch-start", "2026-09-11T12:48:01Z"]
    )
    plan_args = parser.parse_args(
        [
            "plan",
            "--output",
            "plan.json",
            "--planning-epoch-start",
            "2026-09-11T12:48:01Z",
        ]
    )

    cli._run(readiness_args)
    cli._run(plan_args)

    assert captured == [
        ("readiness", PLANNING_EPOCH),
        ("plan", PLANNING_EPOCH),
    ]
