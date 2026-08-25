from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select

from bp_engine.backfill.polymarket import backfill_polymarket_markets
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
    def __init__(self, payloads: dict[str, dict[str, object]]) -> None:
        self.payloads = payloads
        self.calls: list[str] = []

    async def get_market_by_slug(self, slug: str):
        self.calls.append(slug)
        return self.payloads.get(slug)


@pytest.mark.asyncio
async def test_market_backfill_enumerates_exact_btc_slugs_and_records_found_markets() -> None:
    start = datetime(2026, 8, 20, tzinfo=UTC)
    end = start + timedelta(minutes=15)
    downloaded_at = datetime(2026, 8, 24, 22, 0, tzinfo=UTC)

    epoch = int(start.timestamp())
    slug_5m_0 = f"btc-updown-5m-{epoch}"
    slug_5m_1 = f"btc-updown-5m-{epoch + 300}"
    slug_5m_2 = f"btc-updown-5m-{epoch + 600}"
    slug_15m_0 = f"btc-updown-15m-{epoch}"

    client = FakeGammaClient(
        {
            slug_5m_0: gamma_payload(
                market_id="m5",
                slug=slug_5m_0,
                condition_id="condition-5m",
            ),
            slug_15m_0: gamma_payload(
                market_id="m15",
                slug=slug_15m_0,
                condition_id="condition-15m",
            ),
        }
    )
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

    assert client.calls == [slug_5m_0, slug_5m_1, slug_5m_2, slug_15m_0]
    assert stats.rows_inserted == 2
    assert stats.rows_existing == 0
    assert stats.chunks_fetched == 4
    assert market_count == 2
    assert snapshot_count == 2
    assert artifact_count == 2


@pytest.mark.asyncio
async def test_market_backfill_rejects_provider_slug_mismatch() -> None:
    start = datetime(2026, 8, 20, tzinfo=UTC)
    end = start + timedelta(minutes=5)
    requested_slug = f"btc-updown-5m-{int(start.timestamp())}"
    mismatched_slug = f"btc-updown-5m-{int(start.timestamp()) + 300}"
    client = FakeGammaClient(
        {
            requested_slug: gamma_payload(
                market_id="wrong",
                slug=mismatched_slug,
                condition_id="condition-wrong",
            )
        }
    )
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)

    with engine.begin() as connection:
        with pytest.raises(RuntimeError, match="slug mismatch"):
            await backfill_polymarket_markets(
                connection,
                client,
                run_id="run-mismatch",
                start=start,
                end=end,
                horizons=("5m",),
                downloaded_at=datetime(2026, 8, 24, 22, 0, tzinfo=UTC),
            )
