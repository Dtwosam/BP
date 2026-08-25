from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, func, select

from bp_engine.backfill.polymarket import backfill_polymarket_markets
from bp_engine.polymarket.gamma import GammaMarketOffsetPage
from bp_engine.storage.schema import (
    historical_backfill_artifacts,
    metadata,
    polymarket_market_snapshots,
    polymarket_markets,
)


def gamma_payload(*, market_id: str, slug: str, condition_id: str) -> dict[str, object]:
    return {
        "id": market_id,
        "conditionId": condition_id,
        "slug": slug,
        "question": "Bitcoin Up or Down?",
        "outcomes": '["Up", "Down"]',
        "clobTokenIds": f'["{market_id}-up", "{market_id}-down"]',
        "outcomePrices": '["1", "0"]',
        "resolutionSource": "https://data.chain.link/streams/btc-usd",
        "description": "Resolves Up when the stated BTC TWAP is at least the opening value.",
        "active": False,
        "closed": True,
        "acceptingOrders": False,
        "events": [{"id": f"event-{market_id}"}],
    }


class FakeGammaClient:
    def __init__(self, pages: list[GammaMarketOffsetPage]) -> None:
        self.pages = pages
        self.calls: list[int] = []

    async def list_markets_offset_page(
        self,
        *,
        start: datetime,
        end: datetime,
        limit: int = 100,
        offset: int = 0,
    ) -> GammaMarketOffsetPage:
        del start, end, limit
        self.calls.append(offset)
        return self.pages[len(self.calls) - 1]


@pytest.mark.asyncio
async def test_market_backfill_filters_horizons_window_versions_snapshot_and_records_page() -> None:
    start = datetime(2026, 8, 20, tzinfo=UTC)
    end = datetime(2026, 8, 21, tzinfo=UTC)
    downloaded_at = datetime(2026, 8, 24, 22, 0, tzinfo=UTC)
    valid = gamma_payload(
        market_id="m5",
        slug="btc-updown-5m-1787184000",
        condition_id="condition-5m",
    )
    unsupported_horizon = gamma_payload(
        market_id="m10",
        slug="btc-updown-10m-1787184000",
        condition_id="condition-10m",
    )
    outside_window = gamma_payload(
        market_id="m-old",
        slug="btc-updown-5m-1787183700",
        condition_id="condition-old",
    )
    unrelated = {"id": "other", "slug": "some-other-market"}
    raw_page = [valid, unsupported_horizon, outside_window, unrelated]
    page1 = GammaMarketOffsetPage(
        markets=(valid, unsupported_horizon, outside_window, unrelated),
        next_offset=100,
        request_params={"limit": "100", "offset": "0"},
        raw_payload=raw_page,
    )
    page2 = GammaMarketOffsetPage(
        markets=(),
        next_offset=None,
        request_params={"limit": "100", "offset": "100"},
        raw_payload=[],
    )
    client = FakeGammaClient([page1, page2])
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)

    with engine.begin() as connection:
        stats = await backfill_polymarket_markets(
            connection,
            client,
            run_id="run-1",
            start=start,
            end=end,
            horizons=("5m", "15m"),
            downloaded_at=downloaded_at,
        )
        market_count = connection.scalar(select(func.count()).select_from(polymarket_markets))
        snapshot_count = connection.scalar(
            select(func.count()).select_from(polymarket_market_snapshots)
        )
        artifact_count = connection.scalar(
            select(func.count()).select_from(historical_backfill_artifacts)
        )

    assert client.calls == [0, 100]
    assert stats.rows_inserted == 1
    assert stats.rows_existing == 0
    assert stats.chunks_fetched == 2
    assert market_count == 1
    assert snapshot_count == 1
    assert artifact_count == 2
