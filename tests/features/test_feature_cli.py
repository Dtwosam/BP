from __future__ import annotations

import argparse
import importlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, insert

from bp_engine.storage.schema import market_labels, metadata


def _cli():
    return importlib.import_module("bp_engine.features.cli")


def _label_values(start: datetime) -> dict[str, object]:
    return {
        "condition_id": "phase6-cli-static-target",
        "gamma_market_id": "phase6-cli-static-market",
        "slug": "btc-updown-5m-phase6-cli",
        "horizon_seconds": 300,
        "market_start_at": start,
        "market_end_at": start + timedelta(minutes=5),
        "official_outcome": "Up",
        "start_reference": None,
        "end_reference": None,
        "resolution_source": "https://example.invalid/official",
        "rules_hash": "sha256:phase6-cli-rules",
        "label_source": "polymarket_gamma_snapshot",
        "label_version": "official-outcome-v1",
        "source_snapshot_sha256": "sha256:phase6-cli-snapshot",
        "source_observed_at": start + timedelta(minutes=6),
        "generated_at": start + timedelta(minutes=7),
    }


def test_parse_datetime_rejects_naive_value_and_accepts_z() -> None:
    cli = _cli()

    with pytest.raises(argparse.ArgumentTypeError, match="timezone"):
        cli.parse_datetime("2026-08-25T12:00:00")

    assert cli.parse_datetime("2026-08-25T12:00:00Z") == datetime(
        2026, 8, 25, 12, 0, tzinfo=UTC
    )


def test_validate_window_and_step_fail_closed() -> None:
    cli = _cli()
    start = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="start must be before end"):
        cli.validate_window(start, start)
    with pytest.raises(ValueError, match="step_seconds must be positive"):
        cli.validate_step_seconds(0)


def test_load_targets_selects_only_static_label_columns() -> None:
    cli = _cli()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    start = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _capture(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        if statement.lstrip().lower().startswith("select"):
            statements.append(statement.lower())

    with engine.begin() as connection:
        connection.execute(insert(market_labels), [_label_values(start)])
        targets = cli.load_targets(
            connection,
            start=start,
            end=start + timedelta(minutes=10),
        )

    assert len(targets) == 1
    assert targets[0].condition_id == "phase6-cli-static-target"
    assert statements
    query = statements[-1]
    for required in (
        "condition_id",
        "slug",
        "horizon_seconds",
        "market_start_at",
        "market_end_at",
    ):
        assert required in query
    for forbidden in (
        "official_outcome",
        "start_reference",
        "end_reference",
        "resolution_source",
        "label_source",
        "label_version",
        "source_snapshot_sha256",
        "source_observed_at",
    ):
        assert forbidden not in query


def test_feature_cli_has_no_http_or_websocket_client_imports() -> None:
    cli = _cli()
    source = Path(cli.__file__).read_text(encoding="utf-8").lower()

    assert "import httpx" not in source
    assert "from httpx" not in source
    assert "import websockets" not in source
    assert "from websockets" not in source


def test_main_prints_deterministic_batch_stats_for_empty_window(capsys) -> None:
    cli = _cli()
    start = "2026-08-25T12:00:00Z"
    end = "2026-08-25T12:10:00Z"

    assert (
        cli.main(
            [
                "--start",
                start,
                "--end",
                end,
                "--database-url",
                "sqlite+pysqlite:///:memory:",
                "--step-seconds",
                "60",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload == {
        "existing": 0,
        "inserted": 0,
        "missing_group_counts": {},
        "planned_rows": 0,
        "targets_considered": 0,
    }
