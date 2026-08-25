from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select

from bp_engine.storage.historical import (
    BtcCandle,
    HistoricalDataConflict,
    HistoricalRepository,
    PolymarketPricePoint,
)
from bp_engine.storage.schema import btc_candles, metadata, polymarket_price_history


def make_repository():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    return engine, HistoricalRepository()


def test_polymarket_price_insert_is_idempotent_and_conflicts_fail_closed() -> None:
    engine, repository = make_repository()
    point = PolymarketPricePoint(
        condition_id="condition-1",
        asset_id="asset-up",
        outcome="Up",
        observed_at=datetime(2026, 8, 20, 12, 1, tzinfo=UTC),
        price=Decimal("0.6123"),
        fidelity_minutes=1,
        source="polymarket_clob_prices_history",
    )

    with engine.begin() as connection:
        first = repository.store_polymarket_price(connection, point)
        second = repository.store_polymarket_price(connection, point)
        count = connection.scalar(select(func.count()).select_from(polymarket_price_history))

        changed = replace(point, price=Decimal("0.7000"))
        with pytest.raises(HistoricalDataConflict, match="asset-up"):
            repository.store_polymarket_price(connection, changed)

    assert first.created is True
    assert second.created is False
    assert count == 1


def test_btc_candle_insert_is_idempotent_and_market_type_is_part_of_key() -> None:
    engine, repository = make_repository()
    bucket_at = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    base = dict(
        source="bybit",
        symbol="BTCUSDT",
        interval_seconds=60,
        bucket_at=bucket_at,
        open=Decimal("60000"),
        high=Decimal("60100"),
        low=Decimal("59900"),
        close=Decimal("60050"),
        volume=Decimal("12.5"),
        turnover=Decimal("750000"),
        raw_payload=["1787227200000", "60000", "60100", "59900", "60050", "12.5", "750000"],
    )
    spot = BtcCandle(market_type="spot", **base)
    linear = BtcCandle(market_type="linear", **base)

    with engine.begin() as connection:
        first = repository.store_btc_candle(connection, spot)
        duplicate = repository.store_btc_candle(connection, spot)
        second_market = repository.store_btc_candle(connection, linear)
        count = connection.scalar(select(func.count()).select_from(btc_candles))

        changed = replace(spot, close=Decimal("60051"))
        with pytest.raises(HistoricalDataConflict, match="BTCUSDT"):
            repository.store_btc_candle(connection, changed)

    assert first.created is True
    assert duplicate.created is False
    assert second_market.created is True
    assert count == 2
