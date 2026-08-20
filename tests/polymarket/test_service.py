import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select

from bp_engine.config import Settings
from bp_engine.polymarket.service import MarketDiscoveryService
from bp_engine.storage.schema import metadata, polymarket_markets

FIXTURES = Path(__file__).parents[1] / "fixtures" / "polymarket"


class FakeGammaClient:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.requested: list[str] = []

    async def get_market_by_slug(self, slug: str) -> dict[str, object] | None:
        self.requested.append(slug)
        if slug == self.payload["slug"]:
            return self.payload
        return None


@pytest.mark.asyncio
async def test_service_uses_only_active_horizons_and_persists_discovered_market() -> None:
    payload = json.loads((FIXTURES / "btc_updown_5m_gamma.json").read_text())
    settings = Settings(_env_file=None, active_horizons=("5m",), optional_horizons=("10m",))
    client = FakeGammaClient(payload)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    service = MarketDiscoveryService(settings=settings, client=client, engine=engine)

    markets = await service.discover_and_store(datetime(2026, 8, 20, 4, 1, tzinfo=UTC))

    with engine.connect() as connection:
        count = connection.scalar(select(func.count()).select_from(polymarket_markets))

    assert len(markets) == 1
    assert count == 1
    assert all("-5m-" in slug for slug in client.requested)
    assert all("-10m-" not in slug and "-15m-" not in slug for slug in client.requested)
