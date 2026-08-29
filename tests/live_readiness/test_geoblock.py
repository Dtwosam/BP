from datetime import UTC, datetime

import httpx
import pytest

from bp_engine.live_readiness.geoblock import GeoblockClient, GeoblockError

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
URL = "https://polymarket.com/api/geoblock"


def _client(handler) -> GeoblockClient:
    transport = httpx.MockTransport(handler)
    return GeoblockClient(url=URL, client=httpx.Client(transport=transport))


def test_geoblock_parses_official_shape_without_persisting_ip() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == URL
        return httpx.Response(
            200,
            json={"blocked": False, "ip": "203.0.113.7", "country": "NL", "region": "NH"},
        )

    result = _client(handler).check(observed_at=NOW)

    assert result.blocked is False
    assert result.country == "NL"
    assert result.region == "NH"
    assert result.checked_at == NOW
    assert not hasattr(result, "ip")


def test_geoblock_preserves_blocked_status() -> None:
    result = _client(
        lambda request: httpx.Response(
            200,
            json={"blocked": True, "ip": "198.51.100.9", "country": "US", "region": "NY"},
        )
    ).check(observed_at=NOW)
    assert result.blocked is True
    assert result.country == "US"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503, text="unavailable"),
        httpx.Response(200, content=b"not-json"),
        httpx.Response(200, json={"blocked": False, "country": "NL"}),
        httpx.Response(200, json={"blocked": "false", "country": "NL", "region": "NH"}),
    ],
)
def test_geoblock_fails_closed_on_http_or_schema_errors(response: httpx.Response) -> None:
    with pytest.raises(GeoblockError):
        _client(lambda request: response).check(observed_at=NOW)


def test_geoblock_wraps_network_failures_without_route_around() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network unavailable", request=request)

    with pytest.raises(GeoblockError, match="geoblock check failed"):
        _client(handler).check(observed_at=NOW)
