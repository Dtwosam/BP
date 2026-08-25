from datetime import UTC, datetime, timedelta

import pytest

from bp_engine.backfill.bybit import BybitHistoryUnavailableError
from bp_engine.backfill.live_smoke import _bybit_smoke


class UnavailableBybitClient:
    async def get_klines(self, **kwargs):
        del kwargs
        raise BybitHistoryUnavailableError(
            "Bybit REST unavailable from this environment (HTTP 403)"
        )


@pytest.mark.asyncio
async def test_bybit_smoke_reports_classified_http_403_as_environment_limited() -> None:
    start = datetime(2026, 8, 25, 8, 0, tzinfo=UTC)
    report = await _bybit_smoke(
        UnavailableBybitClient(),
        start=start,
        end=start + timedelta(minutes=3),
    )

    assert report == {
        "status": "environment_blocked_http_403",
        "spot_candles": None,
        "linear_candles": None,
    }
