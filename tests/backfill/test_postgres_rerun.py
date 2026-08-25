import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select

from bp_engine.storage.historical import (
    BtcCandle,
    HistoricalDataConflict,
    HistoricalRepository,
)
from bp_engine.storage.schema import btc_candles

DATABASE_URL = os.getenv("BP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BP_TEST_DATABASE_URL is required for PostgreSQL integration coverage",
)


def _apply_historical_migration(database_url: str) -> None:
    engine = create_engine(database_url)
    script = Path("migrations/0004_historical_backfill.sql").read_text(encoding="utf-8")
    with engine.begin() as connection:
        for statement in script.split(";"):
            if statement.strip():
                connection.exec_driver_sql(statement)


def test_postgres_historical_rerun_is_idempotent_and_conflicts_fail_closed() -> None:
    assert DATABASE_URL is not None
    _apply_historical_migration(DATABASE_URL)
    engine = create_engine(DATABASE_URL)
    repository = HistoricalRepository()
    candle = BtcCandle(
        source="phase4-integration",
        market_type="spot",
        symbol="BTCITEST",
        interval_seconds=60,
        bucket_at=datetime(2026, 8, 20, tzinfo=UTC),
        open=Decimal("60000.123456789012"),
        high=Decimal("60100.123456789012"),
        low=Decimal("59900.123456789012"),
        close=Decimal("60050.123456789012"),
        volume=Decimal("12.500000000000000001"),
        turnover=Decimal("750000.000000000000000001"),
        raw_payload=["integration"],
    )

    with engine.begin() as connection:
        connection.execute(
            btc_candles.delete().where(btc_candles.c.source == candle.source)
        )
        first = repository.store_btc_candle(connection, candle)

    with engine.begin() as connection:
        duplicate = repository.store_btc_candle(connection, candle)
        stored = connection.execute(
            select(btc_candles).where(
                btc_candles.c.source == candle.source,
                btc_candles.c.symbol == candle.symbol,
            )
        ).mappings().one()
        count = connection.scalar(
            select(func.count()).select_from(btc_candles).where(
                btc_candles.c.source == candle.source
            )
        )

    changed = BtcCandle(
        **{
            **candle.__dict__,
            "close": Decimal("60051.123456789012"),
        }
    )
    with engine.begin() as connection:
        with pytest.raises(HistoricalDataConflict, match="BTCITEST"):
            repository.store_btc_candle(connection, changed)

    assert first.created is True
    assert duplicate.created is False
    assert count == 1
    assert stored["close"] == candle.close
    assert stored["volume"] == candle.volume
    assert stored["turnover"] == candle.turnover
