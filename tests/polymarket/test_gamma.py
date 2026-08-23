import httpx
import pytest

from bp_engine.polymarket.gamma import GammaClient


@pytest.mark.asyncio
async def test_gamma_client_uses_official_market_by_slug_endpoint() -> None:
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        return httpx.Response(200, json={"id": "market-1", "slug": "btc-updown-5m-123"})

    async with httpx.AsyncClient(
        base_url="https://gamma-api.polymarket.com",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = GammaClient(http_client=http_client)
        payload = await client.get_market_by_slug("btc-updown-5m-123")

    assert seen_paths == ["/markets/slug/btc-updown-5m-123"]
    assert payload == {"id": "market-1", "slug": "btc-updown-5m-123"}


@pytest.mark.asyncio
async def test_gamma_client_returns_none_for_missing_slug() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    async with httpx.AsyncClient(
        base_url="https://gamma-api.polymarket.com",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = GammaClient(http_client=http_client)
        payload = await client.get_market_by_slug("btc-updown-5m-123")

    assert payload is None
