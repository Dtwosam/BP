from __future__ import annotations

from datetime import datetime

from sqlalchemy import Engine

from bp_engine.config import Settings
from bp_engine.polymarket.discovery import GammaClientProtocol, discover_btc_markets
from bp_engine.polymarket.models import PolymarketMarket
from bp_engine.storage.polymarket_markets import PolymarketMarketRepository


class MarketDiscoveryService:
    def __init__(
        self,
        settings: Settings,
        client: GammaClientProtocol,
        engine: Engine,
        repository: PolymarketMarketRepository | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._engine = engine
        self._repository = repository or PolymarketMarketRepository()

    async def discover_and_store(self, now: datetime) -> list[PolymarketMarket]:
        markets = await discover_btc_markets(
            self._client,
            now,
            horizons=self._settings.active_horizons,
        )
        with self._engine.begin() as connection:
            for market in markets:
                self._repository.upsert(connection, market, observed_at=now)
        return markets
