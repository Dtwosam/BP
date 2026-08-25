from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx


class GammaResponseError(ValueError):
    """Raised when Gamma returns a response shape we cannot safely consume."""


@dataclass(frozen=True)
class GammaMarketPage:
    markets: tuple[dict[str, Any], ...]
    next_cursor: str | None
    request_params: dict[str, str]
    raw_payload: dict[str, Any]


class GammaClient:
    BASE_URL = "https://gamma-api.polymarket.com"
    KEYSET_MAX_ATTEMPTS = 3
    KEYSET_RETRY_STATUSES = frozenset({500, 503})
    KEYSET_RETRY_BASE_DELAY_SECONDS = 0.25

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

    async def list_markets_page(
        self,
        *,
        start: datetime,
        end: datetime,
        limit: int = 100,
        after_cursor: str | None = None,
    ) -> GammaMarketPage:
        if start.tzinfo is None or start.utcoffset() is None:
            raise ValueError("start must be timezone-aware")
        if end.tzinfo is None or end.utcoffset() is None:
            raise ValueError("end must be timezone-aware")
        if start >= end:
            raise ValueError("start must be before end")
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")

        params = {
            "limit": str(limit),
            "closed": "true",
            "start_date_min": self._iso_z(start),
            "start_date_max": self._iso_z(end),
        }
        if after_cursor is not None:
            params["after_cursor"] = after_cursor

        if self._http_client is not None:
            response = await self._get_keyset_with_retry(self._http_client, params)
        else:
            async with httpx.AsyncClient(base_url=self.BASE_URL, timeout=10.0) as client:
                response = await self._get_keyset_with_retry(client, params)

        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise GammaResponseError("markets keyset response must be a JSON object")

        markets_payload = payload.get("markets")
        if not isinstance(markets_payload, list):
            raise GammaResponseError("markets keyset response must contain a markets array")

        markets: list[dict[str, Any]] = []
        for market in markets_payload:
            if not isinstance(market, Mapping):
                raise GammaResponseError("markets keyset entries must be JSON objects")
            markets.append(dict(market))

        next_cursor = payload.get("next_cursor")
        if next_cursor is not None and not isinstance(next_cursor, str):
            raise GammaResponseError("next_cursor must be a string when present")

        return GammaMarketPage(
            markets=tuple(markets),
            next_cursor=next_cursor,
            request_params=params,
            raw_payload=dict(payload),
        )

    async def _get_keyset_with_retry(
        self,
        client: httpx.AsyncClient,
        params: dict[str, str],
    ) -> httpx.Response:
        for attempt in range(self.KEYSET_MAX_ATTEMPTS):
            response = await client.get("/markets/keyset", params=params)
            retryable = response.status_code in self.KEYSET_RETRY_STATUSES
            if not retryable or attempt == self.KEYSET_MAX_ATTEMPTS - 1:
                return response
            delay = self.KEYSET_RETRY_BASE_DELAY_SECONDS * (2**attempt)
            await asyncio.sleep(delay)
        raise AssertionError("unreachable Gamma keyset retry state")

    @staticmethod
    def _iso_z(value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
