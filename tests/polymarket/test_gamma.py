from datetime import UTC, datetime

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
async def test_gamma_client_returns_none_for_missing_slug_without_retry() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(404, json={"error": "not found"})

    async with httpx.AsyncClient(
        base_url="https://gamma-api.polymarket.com",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = GammaClient(http_client=http_client)
        payload = await client.get_market_by_slug("btc-updown-5m-123")

    assert payload is None
    assert attempts == 1


@pytest.mark.asyncio
async def test_gamma_market_by_slug_retries_transient_server_error_then_succeeds() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(500, json={"error": "temporary server error"})
        return httpx.Response(200, json={"id": "market-1", "slug": "btc-updown-5m-123"})

    async with httpx.AsyncClient(
        base_url="https://gamma-api.polymarket.com",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        payload = await GammaClient(http_client=http_client).get_market_by_slug(
            "btc-updown-5m-123"
        )

    assert attempts == 2
    assert payload == {"id": "market-1", "slug": "btc-updown-5m-123"}


@pytest.mark.asyncio
async def test_gamma_client_lists_closed_markets_with_keyset_date_filters() -> None:
    seen: list[tuple[str, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, dict(request.url.params)))
        return httpx.Response(
            200,
            json={
                "markets": [{"id": "market-1", "slug": "btc-updown-5m-1787227200"}],
                "next_cursor": "cursor-2",
            },
        )

    async with httpx.AsyncClient(
        base_url="https://gamma-api.polymarket.com",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = GammaClient(http_client=http_client)
        page = await client.list_markets_page(
            start=datetime(2026, 8, 20, tzinfo=UTC),
            end=datetime(2026, 8, 21, tzinfo=UTC),
            limit=100,
            after_cursor="cursor-1",
        )

    assert seen == [
        (
            "/markets/keyset",
            {
                "limit": "100",
                "closed": "true",
                "start_date_min": "2026-08-20T00:00:00Z",
                "start_date_max": "2026-08-21T00:00:00Z",
                "after_cursor": "cursor-1",
            },
        )
    ]
    assert page.markets == (
        {"id": "market-1", "slug": "btc-updown-5m-1787227200"},
    )
    assert page.next_cursor == "cursor-2"
    assert page.request_params == seen[0][1]


@pytest.mark.asyncio
async def test_gamma_keyset_retries_documented_server_error_then_succeeds() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(500, json={"error": "temporary server error"})
        return httpx.Response(
            200,
            json={
                "markets": [{"id": "market-1", "slug": "btc-updown-5m-1787227200"}],
                "next_cursor": None,
            },
        )

    async with httpx.AsyncClient(
        base_url="https://gamma-api.polymarket.com",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        page = await GammaClient(http_client=http_client).list_markets_page(
            start=datetime(2026, 8, 20, tzinfo=UTC),
            end=datetime(2026, 8, 21, tzinfo=UTC),
        )

    assert attempts == 2
    assert page.markets[0]["id"] == "market-1"


@pytest.mark.asyncio
async def test_gamma_keyset_does_not_retry_client_validation_errors() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(422, json={"error": "invalid query"})

    async with httpx.AsyncClient(
        base_url="https://gamma-api.polymarket.com",
        transport=httpx.MockTransport(handler),
    ) as http_client:
        with pytest.raises(httpx.HTTPStatusError):
            await GammaClient(http_client=http_client).list_markets_page(
                start=datetime(2026, 8, 20, tzinfo=UTC),
                end=datetime(2026, 8, 21, tzinfo=UTC),
            )

    assert attempts == 1
