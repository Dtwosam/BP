from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from bp_engine.backfill import cli as historical_backfill
from bp_engine.backfill.bybit import BybitHistoryUnavailableError
from bp_engine.storage.schema import metadata


@pytest.mark.asyncio
async def test_standard_continues_when_bybit_is_explicitly_unavailable(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    calls: list[str] = []

    async def fake_execute_dataset(name, *args, **kwargs):
        del args, kwargs
        calls.append(name)
        if name.startswith("bybit_"):
            raise BybitHistoryUnavailableError(
                "Bybit REST unavailable from this environment (HTTP 403)"
            )
        return historical_backfill.BackfillStats(rows_inserted=1, chunks_fetched=1)

    monkeypatch.setattr(historical_backfill, "_execute_dataset", fake_execute_dataset)
    start = datetime(2026, 8, 20, tzinfo=UTC)
    end = datetime(2026, 8, 20, 1, tzinfo=UTC)
    settings = SimpleNamespace(active_horizons=("5m", "15m"))

    results = await historical_backfill._run_standard(
        engine=engine,
        start=start,
        end=end,
        settings=settings,
        downloaded_at=datetime(2026, 8, 24, 22, 45, tzinfo=UTC),
        fidelity_minutes=1,
        interval_seconds=60,
        require_bybit=False,
    )

    assert calls == list(historical_backfill.STANDARD_SEQUENCE)
    assert results["polymarket_markets"]["status"] == "success"
    assert results["polymarket_prices"]["status"] == "success"
    assert results["coinbase_spot"]["status"] == "success"
    assert results["bybit_spot"]["status"] == "unavailable"
    assert results["bybit_linear"]["status"] == "unavailable"
    assert "HTTP 403" in results["bybit_spot"]["reason"]


@pytest.mark.asyncio
async def test_standard_can_require_bybit_and_fail_closed(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)

    async def fake_execute_dataset(name, *args, **kwargs):
        del args, kwargs
        if name.startswith("bybit_"):
            raise BybitHistoryUnavailableError(
                "Bybit REST unavailable from this environment (HTTP 403)"
            )
        return historical_backfill.BackfillStats(rows_inserted=1, chunks_fetched=1)

    monkeypatch.setattr(historical_backfill, "_execute_dataset", fake_execute_dataset)
    start = datetime(2026, 8, 20, tzinfo=UTC)
    settings = SimpleNamespace(active_horizons=("5m", "15m"))

    with pytest.raises(BybitHistoryUnavailableError, match="HTTP 403"):
        await historical_backfill._run_standard(
            engine=engine,
            start=start,
            end=datetime(2026, 8, 20, 1, tzinfo=UTC),
            settings=settings,
            downloaded_at=datetime(2026, 8, 24, 22, 45, tzinfo=UTC),
            fidelity_minutes=1,
            interval_seconds=60,
            require_bybit=True,
        )
