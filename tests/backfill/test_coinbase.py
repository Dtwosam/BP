from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from bp_engine.backfill.coinbase import (
    CoinbaseCandleResponse,
    CoinbaseHistoryClient,
    backfill_coinbase_candles,
)
from sqlalchemy import create_engine, func, select

from bp_engine.storage.schema import btc_candles, historical_backfill_artifacts, metadata


@pytest.mark.asyncio
async def test_coinbase_client_uses_public_product_candles_and_exact_decimals() -> None:
    seen: list[tuple[str, dict[str, str]]] = []
    start = datetime(2026, 8, 20, tzinfo=UTC)
    end = start + timedelta(minutes=3)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, dict(request.url.params)))
        return httpx.Response(
            200,
            json={
                "candles": [
                    {
                        "start": str(int((start + timedelta(minutes=2)).timestamp())),
                        "low": "99.50",
                        "high": "103.25",
                        "open": "100.10",
                        "close": "102.75",
                        "volume": "1.23456789",
                    },
                    {
                        "start": str(int(start.timestamp())),
                        "low": "98.25",
                        "high": "101.00",
                        "open": "99.00",
                        "close": "100.50",
                        "volume": "2.00000001",
                    },
                ]
            },
        )

    async with httpx.AsyncClient(
        base_url="https://api.coinbase.com",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        response = await CoinbaseHistoryClient(http_client=http_client).get_candles(
            product_id="BTC-USD",
            granularity="ONE_MINUTE",
            start=start,
            end=end,
            limit=350,
        )

    assert seen == [
        (
            "/api/v3/brokerage/market/products/BTC-USD/candles",
            {
                "start": str(int(start.timestamp())),
                "end": str(int(end.timestamp())),
                "granularity": "ONE_MINUTE",
                "limit": "350",
            },
        )
    ]
    assert [candle.bucket_at for candle in response.candles] == [
        start,
        start + timedelta(minutes=2),
    ]
    assert response.candles[0].open == Decimal("99.00")
    assert response.candles[1].close == Decimal("102.75")
    assert response.candles[1].volume == Decimal("1.23456789")


class FakeCoinbaseClient:
    async def get_candles(
        self,
        *,
        product_id: str,
        granularity: str,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> CoinbaseCandleResponse:
        del limit
        rows = []
        cursor = start
        while cursor < end:
            # Deliberately omit one bucket. Coinbase documents that intervals with no ticks
            # may be absent; the backfill must preserve that gap instead of synthesizing data.
            if cursor != start + timedelta(minutes=1):
                rows.append(
                    {
                        "start": str(int(cursor.timestamp())),
                        "low": "99",
                        "high": "102",
                        "open": "100",
                        "close": "101",
                        "volume": "2.5",
                    }
                )
            cursor += timedelta(minutes=1)
        raw = {"candles": list(reversed(rows))}
        return CoinbaseCandleResponse.from_payload(
            raw,
            request_params={
                "product_id": product_id,
                "start": str(int(start.timestamp())),
                "end": str(int(end.timestamp())),
                "granularity": granularity,
                "limit": "350",
            },
        )


@pytest.mark.asyncio
async def test_coinbase_backfill_preserves_empty_buckets_and_is_idempotent() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    start = datetime(2026, 8, 20, tzinfo=UTC)
    end = start + timedelta(minutes=4)
    downloaded_at = datetime(2026, 8, 24, 22, 30, tzinfo=UTC)
    client = FakeCoinbaseClient()

    with engine.begin() as connection:
        first = await backfill_coinbase_candles(
            connection,
            client,
            run_id="run-coinbase",
            product_id="BTC-USD",
            start=start,
            end=end,
            downloaded_at=downloaded_at,
            interval_seconds=60,
            max_candles_per_request=2,
        )
        second = await backfill_coinbase_candles(
            connection,
            client,
            run_id="run-coinbase",
            product_id="BTC-USD",
            start=start,
            end=end,
            downloaded_at=downloaded_at,
            interval_seconds=60,
            max_candles_per_request=2,
        )
        candle_count = connection.scalar(select(func.count()).select_from(btc_candles))
        artifact_count = connection.scalar(
            select(func.count()).select_from(historical_backfill_artifacts)
        )

    assert first.rows_inserted == 3
    assert first.rows_existing == 0
    assert first.chunks_fetched == 2
    assert second.rows_inserted == 0
    assert second.rows_existing == 3
    assert second.chunks_fetched == 2
    assert candle_count == 3
    assert artifact_count == 2
