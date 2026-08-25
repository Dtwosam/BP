from datetime import UTC, datetime, timedelta

import httpx
import pytest

from bp_engine.backfill.bybit import BybitHistoryClient, BybitHistoryUnavailableError


@pytest.mark.asyncio
async def test_bybit_client_classifies_http_403_as_environment_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, request=request, text="Forbidden")

    start = datetime(2026, 8, 20, tzinfo=UTC)
    async with httpx.AsyncClient(
        base_url="https://api.bybit.com",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = BybitHistoryClient(http_client=http_client)
        with pytest.raises(BybitHistoryUnavailableError, match="HTTP 403"):
            await client.get_klines(
                category="spot",
                symbol="BTCUSDT",
                interval="1",
                start=start,
                end=start + timedelta(minutes=1),
                limit=1000,
            )
