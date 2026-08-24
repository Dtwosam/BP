from argparse import ArgumentTypeError
from datetime import UTC, datetime

import pytest

from scripts import historical_backfill


def test_parse_datetime_requires_timezone_and_normalizes_to_utc() -> None:
    with pytest.raises(ArgumentTypeError, match="timezone"):
        historical_backfill.parse_datetime("2026-08-20T00:00:00")

    parsed = historical_backfill.parse_datetime("2026-08-20T02:00:00+02:00")
    assert parsed == datetime(2026, 8, 20, tzinfo=UTC)


def test_validate_window_rejects_empty_or_inverted_ranges() -> None:
    at = datetime(2026, 8, 20, tzinfo=UTC)
    with pytest.raises(ValueError, match="before"):
        historical_backfill.validate_window(at, at)
    with pytest.raises(ValueError, match="before"):
        historical_backfill.validate_window(at, datetime(2026, 8, 19, tzinfo=UTC))


@pytest.mark.asyncio
async def test_standard_sequence_runs_all_datasets_in_fixed_order(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_run_named_dataset(name, **kwargs):
        del kwargs
        calls.append(name)
        return {"dataset": name, "status": "success"}

    monkeypatch.setattr(historical_backfill, "_run_named_dataset", fake_run_named_dataset)
    start = datetime(2026, 8, 20, tzinfo=UTC)
    end = datetime(2026, 8, 21, tzinfo=UTC)

    results = await historical_backfill._run_standard(
        engine=object(),
        start=start,
        end=end,
        settings=object(),
        downloaded_at=datetime(2026, 8, 24, 22, 45, tzinfo=UTC),
        fidelity_minutes=1,
        interval_seconds=60,
    )

    assert calls == [
        "polymarket_markets",
        "polymarket_prices",
        "bybit_spot",
        "bybit_linear",
        "coinbase_spot",
    ]
    assert list(results) == calls


def test_parser_exposes_required_commands_and_explicit_window() -> None:
    parser = historical_backfill.build_parser()
    args = parser.parse_args(
        [
            "standard",
            "--start",
            "2026-08-20T00:00:00Z",
            "--end",
            "2026-08-21T00:00:00Z",
        ]
    )
    assert args.command == "standard"
    assert args.start == datetime(2026, 8, 20, tzinfo=UTC)
    assert args.end == datetime(2026, 8, 21, tzinfo=UTC)
