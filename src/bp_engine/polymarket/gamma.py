from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

import httpx


class GammaResponseError(ValueError):
    """Raised when Gamma returns a response shape we cannot safely consume."""


class GammaClient:
    BASE_URL = "https://gamma-api.polymarket.com"

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._http_client = http_client

    async def get_market_by_slug(self, slug: str) -> dict[str, Any] | None:
        path = f"/markets/slug/{quote(slug, safe='')}"
        if self._http_client is not None:
            response = await self._http_client.get(path)
        else:
            async with httpx.AsyncClient(base_url=self.BASE_URL, timeout=10.0) as client:
                response = await client.get(path)

        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise GammaResponseError("market-by-slug response must be a JSON object")
        return dict(payload)
