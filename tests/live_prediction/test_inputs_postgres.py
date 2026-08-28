from __future__ import annotations

import importlib
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, delete, func, insert, select

from bp_engine.backfill.polymarket_prices import PriceHistoryResponse
from bp_engine.storage.schema import market_state_1s, metadata, polymarket_price_history

DATABASE_URL = os.getenv("BP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BP_TEST_DATABASE_URL is required for PostgreSQL integration coverage",
)


class EmptyPriceHistoryClient:
    def __init__(self, scheduled_at: datetime) -> None:
        self.scheduled_at = scheduled_at

    async def get_history(
        self,
        asset_id: str,
        *,
        start: datetime,
        end: datetime,
        fidelity_minutes: int,
    ) -> PriceHistoryResponse:
        assert asset_id == "pg-up-token"
        assert end == self.scheduled_at
        assert fidelity_minutes == 1
        return PriceHistoryResponse(
            points=(),
            request_params={
                "market": asset_id,
                "startTs": str(int(start.timestamp())),
                "endTs": str(int(end.timestamp())),
                "fidelity": "1",
            },
            raw_payload={"history": []},
        )


@pytest.mark.asyncio
async def test_postgres_observer_uses_safe_token_states_without_writing_historical_prices() -> None:
    assert DATABASE_URL is not None
    module = importlib.import_module("bp_engine.live_prediction.inputs")
    engine = create_engine(DATABASE_URL)
    metadata.create_all(engine)
    start = datetime(2026, 8, 26, 13, 0, tzinfo=UTC)
    end = start + timedelta(minutes=5)
    scheduled = start + timedelta(minutes=4)
    condition_id = "phase10-input-postgres"
    assets = ("pg-up-token", "pg-down-token")

    with engine.begin() as connection:
        connection.execute(
            delete(market_state_1s).where(market_state_1s.c.instrument == condition_id)
        )
        before = connection.scalar(select(func.count()).select_from(polymarket_price_history))
        assert before is not None

        connection.execute(
            insert(market_state_1s),
            [
                {
                    "bucket_at": scheduled - timedelta(seconds=1),
                    "state_key": f"polymarket/market/{condition_id}/pg-up-token-safe",
                    "source": "polymarket",
                    "stream": "market",
                    "instrument": condition_id,
                    "market_id": condition_id,
                    "asset_id": "pg-up-token",
                    "last_event_at": scheduled - timedelta(seconds=1),
                    "state": {"best_bid": "0.57", "best_ask": "0.59"},
                },
                {
                    "bucket_at": scheduled,
                    "state_key": f"polymarket/market/{condition_id}/pg-up-token-future",
                    "source": "polymarket",
                    "stream": "market",
                    "instrument": condition_id,
                    "market_id": condition_id,
                    "asset_id": "pg-up-token",
                    "last_event_at": scheduled + timedelta(microseconds=1),
                    "state": {"best_bid": "0.01", "best_ask": "0.99"},
                },
                {
                    "bucket_at": scheduled,
                    "state_key": f"polymarket/market/{condition_id}/pg-down-token",
                    "source": "polymarket",
                    "stream": "market",
                    "instrument": condition_id,
                    "market_id": condition_id,
                    "asset_id": "pg-down-token",
                    "last_event_at": scheduled,
                    "state": {"best_bid": "0.40", "best_ask": "0.42"},
                },
            ],
        )

        live_input = await module.observe_live_input(
            connection,
            EmptyPriceHistoryClient(scheduled),
            condition_id=condition_id,
            up_token_id=assets[0],
            down_token_id=assets[1],
            market_start_at=start,
            market_end_at=end,
            scheduled_at=scheduled,
            clock=lambda: scheduled + timedelta(seconds=1),
        )
        after = connection.scalar(select(func.count()).select_from(polymarket_price_history))
        connection.execute(
            delete(market_state_1s).where(market_state_1s.c.instrument == condition_id)
        )

    assert after == before
    assert live_input.market_probability_observed is False
    assert live_input.predictors["pm_up_price"] is None
    assert live_input.up_book is not None
    assert live_input.up_book.last_event_at == scheduled - timedelta(seconds=1)
    assert live_input.predictors["pm_up_best_ask"] == pytest.approx(0.59)
    assert live_input.predictors["pm_down_best_ask"] == pytest.approx(0.42)
