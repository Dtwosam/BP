from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import create_engine, func, select

from bp_engine.backfill.bybit import (
    BybitHistoryClient,
    BybitKlineResponse,
    backfill_bybit_candles,
    iter_candle_windows,
)
from bp_engine.storage.schema import btc_candles, historical_backfill_artifacts, metadata


def test_iter_candle_windows_is_deterministic_and_non_overlapping() -> None:
    start = datetime(2026, 8, 20, tzinfo=UTC)
    end = start + timedelta(minutes=5)

    assert tuple(
        iter_candle_windows(start, end, interval_seconds=60, max_candles=2)
    ) == (
        (start, start + timedelta(minutes=2)),
        (start + timedelta(minutes=2), start + timedelta(minutes=4)),
        (start + timedelta(minutes=4), end),
    )


@pytest.mark.asyncio
async def test_bybit_client_uses_v5_kline_and_normalizes_reverse_rows() -> None:
    seen: list[tuple[str, dict[str, str]]] = []
    start = datetime(2026, 8, 20, tzinfo=UTC)
    end = start + timedelta(minutes=2)
    first_ms = int(start.timestamp() * 1000)
    second_ms = int((start + timedelta(minutes=1)).timestamp() * 1000)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, dict(request.url.params)))
        return httpx.Response(
            200,
            json={
                "retCode": 0,
                "retMsg": "OK",
                "result": {
                    "category": "spot",
                    "symbol": "BTCUSDT",
                    "list": [
                        [str(second_ms), "101", "103", "100", "102", "2.5", "255"],
                        [str(first_ms), "100", "102", "99", "101", "2", "202"],
                    ],
                },
            },
        )

    async with httpx.AsyncClient(
        base_url="https://api.bybit.com",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        response = await BybitHistoryClient(http_client=http_client).get_klines(
            category="spot",
            symbol="BTCUSDT",
            interval="1",
            start=start,
            end=end,
            limit=1000,
        )

    assert seen == [
        (
            "/v5/market/kline",
            {
                "category": "spot",
                "symbol": "BTCUSDT",
                "interval": "1",
                "start": str(first_ms),
                "end": str(int(end.timestamp() * 1000) - 1),
                "limit": "1000",
            },
        )
    ]
    assert [candle.bucket_at for candle in response.candles] == [
        start,
        start + timedelta(minutes=1),
    ]
    assert response.candles[0].open == Decimal("100")
    assert response.candles[1].close == Decimal("102")


class FakeBybitClient:
    async def get_klines(
        self,
        *,
        category: str,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> BybitKlineResponse:
        del limit
        rows = []
        cursor = start
        while cursor < end:
            rows.append(
                [
                    str(int(cursor.timestamp() * 1000)),
                    "100",
                    "102",
                    "99",
                    "101",
                    "2",
                    "202",
                ]
            )
            cursor += timedelta(minutes=1)
        raw = {
            "retCode": 0,
            "retMsg": "OK",
            "result": {"category": category, "symbol": symbol, "list": list(reversed(rows))},
        }
        return BybitKlineResponse.from_payload(
            raw,
            request_params={
                "category": category,
                "symbol": symbol,
                "interval": interval,
                "start": str(int(start.timestamp() * 1000)),
                "end": str(int(end.timestamp() * 1000) - 1),
                "limit": "1000",
            },
        )


@pytest.mark.asyncio
async def test_bybit_backfill_separates_spot_and_linear_and_is_idempotent() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    start = datetime(2026, 8, 20, tzinfo=UTC)
    end = start + timedelta(minutes=3)
    downloaded_at = datetime(2026, 8, 24, 22, 0, tzinfo=UTC)
    client = FakeBybitClient()

    with engine.begin() as connection:
        spot_first = await backfill_bybit_candles(
            connection,
            client,
            run_id="run-bybit",
            category="spot",
            symbol="BTCUSDT",
            start=start,
            end=end,
            downloaded_at=downloaded_at,
            interval_seconds=60,
            max_candles_per_request=2,
        )
        linear_first = await backfill_bybit_candles(
            connection,
            client,
            run_id="run-bybit",
            category="linear",
            symbol="BTCUSDT",
            start=start,
            end=end,
            downloaded_at=downloaded_at,
            interval_seconds=60,
            max_candles_per_request=2,
        )
        spot_second = await backfill_bybit_candles(
            connection,
            client,
            run_id="run-bybit",
            category="spot",
            symbol="BTCUSDT",
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

    assert spot_first.rows_inserted == 3
    assert linear_first.rows_inserted == 3
    assert spot_second.rows_inserted == 0
    assert spot_second.rows_existing == 3
    assert candle_count == 6
    assert artifact_count == 4
