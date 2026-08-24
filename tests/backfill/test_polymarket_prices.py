from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import create_engine, func, select

from bp_engine.backfill.polymarket_prices import (
    PolymarketPriceHistoryClient,
    PriceHistoryResponse,
    backfill_polymarket_prices,
)
from bp_engine.polymarket.parsing import parse_gamma_market
from bp_engine.storage.polymarket_markets import PolymarketMarketRepository
from bp_engine.storage.schema import (
    historical_backfill_artifacts,
    metadata,
    polymarket_price_history,
)


@pytest.mark.asyncio
async def test_price_history_client_uses_asset_window_and_fidelity() -> None:
    seen: list[tuple[str, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, dict(request.url.params)))
        return httpx.Response(200, json={"history": [{"t": 1755648060, "p": 0.6123}]})

    async with httpx.AsyncClient(
        base_url="https://clob.polymarket.com",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = PolymarketPriceHistoryClient(http_client=http_client)
        response = await client.get_history(
            "asset-up",
            start=datetime(2026, 8, 20, tzinfo=UTC),
            end=datetime(2026, 8, 20, 0, 5, tzinfo=UTC),
            fidelity_minutes=1,
        )

    assert seen == [
        (
            "/prices-history",
            {
                "market": "asset-up",
                "startTs": "1787184000",
                "endTs": "1787184300",
                "fidelity": "1",
            },
        )
    ]
    assert response.points[0].observed_at == datetime.fromtimestamp(1755648060, tz=UTC)
    assert response.points[0].price == Decimal("0.6123")
    assert response.request_params == seen[0][1]


def gamma_payload() -> dict[str, object]:
    return {
        "id": "market-1",
        "conditionId": "condition-1",
        "slug": "btc-updown-5m-1787184000",
        "question": "Bitcoin Up or Down?",
        "outcomes": '["Up", "Down"]',
        "clobTokenIds": '["asset-up", "asset-down"]',
        "outcomePrices": '["1", "0"]',
        "resolutionSource": "https://data.chain.link/streams/btc-usd",
        "description": "Resolves Up when the BTC TWAP is at least the opening value.",
        "active": False,
        "closed": True,
        "acceptingOrders": False,
    }


class FakePriceClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get_history(
        self,
        asset_id: str,
        *,
        start: datetime,
        end: datetime,
        fidelity_minutes: int,
    ) -> PriceHistoryResponse:
        self.calls.append(asset_id)
        base = Decimal("0.6") if asset_id == "asset-up" else Decimal("0.4")
        raw = {
            "history": [
                {"t": int(start.timestamp()) + 60, "p": float(base)},
                {"t": int(start.timestamp()) + 120, "p": float(base + Decimal("0.01"))},
            ]
        }
        return PriceHistoryResponse.from_payload(
            raw,
            request_params={
                "market": asset_id,
                "startTs": str(int(start.timestamp())),
                "endTs": str(int(end.timestamp())),
                "fidelity": str(fidelity_minutes),
            },
        )


@pytest.mark.asyncio
async def test_price_backfill_stores_both_outcomes_idempotently_with_artifacts() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    market = parse_gamma_market(gamma_payload())
    observed_at = datetime(2026, 8, 24, 22, 0, tzinfo=UTC)
    start = datetime(2026, 8, 20, tzinfo=UTC)
    end = datetime(2026, 8, 21, tzinfo=UTC)
    client = FakePriceClient()

    with engine.begin() as connection:
        PolymarketMarketRepository().upsert(connection, market, observed_at)
        first = await backfill_polymarket_prices(
            connection,
            client,
            run_id="run-price-1",
            start=start,
            end=end,
            downloaded_at=observed_at,
            fidelity_minutes=1,
        )
        second = await backfill_polymarket_prices(
            connection,
            client,
            run_id="run-price-1",
            start=start,
            end=end,
            downloaded_at=observed_at,
            fidelity_minutes=1,
        )
        price_count = connection.scalar(
            select(func.count()).select_from(polymarket_price_history)
        )
        artifact_count = connection.scalar(
            select(func.count()).select_from(historical_backfill_artifacts)
        )

    assert client.calls == ["asset-up", "asset-down", "asset-up", "asset-down"]
    assert first.rows_inserted == 4
    assert first.rows_existing == 0
    assert first.chunks_fetched == 2
    assert second.rows_inserted == 0
    assert second.rows_existing == 4
    assert second.chunks_fetched == 2
    assert price_count == 4
    assert artifact_count == 2
