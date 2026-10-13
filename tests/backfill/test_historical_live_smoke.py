from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from bp_engine.backfill.bybit import BybitKlineResponse
from bp_engine.backfill.coinbase import CoinbaseCandleResponse
from bp_engine.backfill.live_smoke import find_recent_closed_btc_market, run_live_source_smoke
from bp_engine.backfill.polymarket_prices import PriceHistoryPoint, PriceHistoryResponse


def gamma_payload(slug: str) -> dict[str, object]:
    return {
        "id": "market-1",
        "conditionId": "condition-1",
        "slug": slug,
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


class FakeGammaClient:
    def __init__(self, expected_slug: str) -> None:
        self.expected_slug = expected_slug
        self.calls: list[str] = []

    async def get_market_by_slug(self, slug: str):
        self.calls.append(slug)
        if slug == self.expected_slug:
            return gamma_payload(slug)
        return None


@pytest.mark.asyncio
async def test_recent_market_probe_uses_completed_aligned_slugs() -> None:
    now = datetime(2026, 8, 24, 22, 47, tzinfo=UTC)
    # The 22:35 UTC five-minute market is completed and old enough for settlement lag.
    expected_epoch = int(datetime(2026, 8, 24, 22, 35, tzinfo=UTC).timestamp())
    expected_slug = f"btc-updown-5m-{expected_epoch}"
    client = FakeGammaClient(expected_slug)

    market = await find_recent_closed_btc_market(client, now=now)

    assert market.slug == expected_slug
    assert client.calls[0].startswith("btc-updown-5m-")
    assert market.closed is True


class FakePriceClient:
    async def get_history(self, asset_id, *, start, end, fidelity_minutes):
        del end
        return PriceHistoryResponse(
            points=(PriceHistoryPoint(start + timedelta(minutes=1), Decimal("0.5")),),
            request_params={"market": asset_id, "fidelity": str(fidelity_minutes)},
            raw_payload={"history": [{"t": int(start.timestamp()) + 60, "p": 0.5}]},
        )


class FakeBybitClient:
    async def get_klines(self, *, category, symbol, interval, start, end, limit):
        del category, symbol, interval, limit
        row = [
            str(int(start.timestamp() * 1000)),
            "100",
            "102",
            "99",
            "101",
            "2",
            "202",
        ]
        return BybitKlineResponse.from_payload(
            {
                "retCode": 0,
                "retMsg": "OK",
                "result": {"category": "spot", "symbol": "BTCUSDT", "list": [row]},
            },
            request_params={"start": str(int(start.timestamp())), "end": str(int(end.timestamp()))},
        )


class BlockedBybitClient:
    async def get_klines(self, **kwargs):
        del kwargs
        request = httpx.Request("GET", "https://api.bybit.com/v5/market/kline")
        response = httpx.Response(403, request=request)
        raise httpx.HTTPStatusError("blocked", request=request, response=response)


class FakeCoinbaseClient:
    async def get_candles(self, *, product_id, granularity, start, end, limit):
        del product_id, granularity, limit
        return CoinbaseCandleResponse.from_payload(
            {
                "candles": [
                    {
                        "start": str(int(start.timestamp())),
                        "low": "99",
                        "high": "102",
                        "open": "100",
                        "close": "101",
                        "volume": "2",
                    }
                ]
            },
            request_params={"start": str(int(start.timestamp())), "end": str(int(end.timestamp()))},
        )


def _gamma_for(now: datetime) -> FakeGammaClient:
    aligned = int(now.timestamp()) - (int(now.timestamp()) % 300)
    return FakeGammaClient(f"btc-updown-5m-{aligned - 600}")


@pytest.mark.asyncio
async def test_live_source_smoke_returns_sanitized_nonempty_counts() -> None:
    now = datetime(2026, 8, 24, 22, 47, tzinfo=UTC)
    report = await run_live_source_smoke(
        now=now,
        gamma_client=_gamma_for(now),
        price_client=FakePriceClient(),
        bybit_client=FakeBybitClient(),
        coinbase_client=FakeCoinbaseClient(),
    )

    assert report["status"] == "ok"
    assert report["polymarket"]["up_price_points"] == 1
    assert report["polymarket"]["down_price_points"] == 1
    assert "historical_listing_markets" not in report["polymarket"]
    assert report["bybit"]["status"] == "ok"
    assert report["bybit"]["spot_candles"] == 1
    assert report["bybit"]["linear_candles"] == 1
    assert report["coinbase"]["spot_candles"] == 1
    assert "asset-up" not in str(report)
    assert "asset-down" not in str(report)


@pytest.mark.asyncio
async def test_live_source_smoke_marks_bybit_403_as_environment_limited() -> None:
    now = datetime(2026, 8, 24, 22, 47, tzinfo=UTC)
    report = await run_live_source_smoke(
        now=now,
        gamma_client=_gamma_for(now),
        price_client=FakePriceClient(),
        bybit_client=BlockedBybitClient(),
        coinbase_client=FakeCoinbaseClient(),
    )

    assert report["status"] == "environment_limited"
    assert report["bybit"] == {
        "status": "environment_blocked_http_403",
        "spot_candles": None,
        "linear_candles": None,
    }
    assert report["coinbase"]["status"] == "ok"


class MultiClosedGammaClient:
    def __init__(self, slugs: set[str]) -> None:
        self.slugs = slugs
        self.calls: list[str] = []

    async def get_market_by_slug(self, slug: str):
        self.calls.append(slug)
        return gamma_payload(slug) if slug in self.slugs else None


class SelectivePriceClient:
    def __init__(
        self, available: set[tuple[int, str]], *, error: bool = False
    ) -> None:
        self.available = available
        self.calls: list[tuple[int, str]] = []
        self.error = error

    async def get_history(self, asset_id, *, start, end, fidelity_minutes):
        assert fidelity_minutes == 1
        assert end > start
        key = (int(start.timestamp()), asset_id)
        self.calls.append(key)
        if self.error:
            raise RuntimeError("real CLOB price API failure")
        points = (
            (PriceHistoryPoint(start + timedelta(minutes=1), Decimal("0.5")),)
            if key in self.available else ()
        )
        return PriceHistoryResponse(
            points=points,
            request_params={"market": asset_id, "fidelity": "1"},
            raw_payload={"history": []},
        )


def _recent_slugs(now: datetime, minutes: int, count: int) -> list[str]:
    span = minutes * 60
    aligned = int(now.timestamp()) // span * span
    return [
        f"btc-updown-{minutes}m-{aligned - (offset + 2) * span}"
        for offset in range(count)
    ]


def _start(slug: str) -> int:
    return int(slug.rsplit("-", 1)[1])


@pytest.mark.asyncio
async def test_historical_smoke_rejects_empty_first_market_then_checks_second() -> None:
    now = datetime(2026, 8, 24, 22, 47, tzinfo=UTC)
    first, second = _recent_slugs(now, 5, 2)
    gamma = MultiClosedGammaClient({first, second})
    prices = SelectivePriceClient({
        (_start(first), "asset-up"),
        (_start(second), "asset-up"),
        (_start(second), "asset-down"),
    })
    report = await run_live_source_smoke(
        now=now,
        gamma_client=gamma,
        price_client=prices,
        bybit_client=FakeBybitClient(),
        coinbase_client=FakeCoinbaseClient(),
    )
    assert report["status"] == "ok"
    assert report["polymarket"]["slug"] == second
    assert report["polymarket"]["up_price_points"] == 1
    assert report["polymarket"]["down_price_points"] == 1
    assert report["polymarket"]["historical_price_probe"] == {
        "candidate_markets_checked": 2,
        "empty_up_candidates": 0,
        "empty_down_candidates": 1,
        "closed_5m_markets_checked": 2,
        "closed_15m_markets_checked": 0,
    }
    assert len(prices.calls) == 4
    assert "asset-up" not in str(report)
    assert "asset-down" not in str(report)


@pytest.mark.asyncio
async def test_historical_smoke_can_verify_a_real_15m_market_after_empty_5m() -> None:
    now = datetime(2026, 8, 24, 22, 47, tzinfo=UTC)
    five = _recent_slugs(now, 5, 3)
    fifteen = _recent_slugs(now, 15, 1)[0]
    prices = SelectivePriceClient({
        (_start(fifteen), "asset-up"),
        (_start(fifteen), "asset-down"),
    })
    report = await run_live_source_smoke(
        now=now,
        gamma_client=MultiClosedGammaClient(set(five) | {fifteen}),
        price_client=prices,
        bybit_client=FakeBybitClient(),
        coinbase_client=FakeCoinbaseClient(),
    )
    assert report["polymarket"]["slug"] == fifteen
    assert report["polymarket"]["horizon_seconds"] == 900
    probe = report["polymarket"]["historical_price_probe"]
    assert probe["candidate_markets_checked"] == 4
    assert probe["closed_5m_markets_checked"] == 3
    assert probe["closed_15m_markets_checked"] == 1
    assert probe["empty_up_candidates"] == 3
    assert probe["empty_down_candidates"] == 3


@pytest.mark.asyncio
async def test_historical_smoke_fails_closed_when_both_series_unavailable() -> None:
    now = datetime(2026, 8, 24, 22, 47, tzinfo=UTC)
    five = _recent_slugs(now, 5, 4)
    fifteen = _recent_slugs(now, 15, 4)
    prices = SelectivePriceClient(set())
    gamma = MultiClosedGammaClient(set(five) | set(fifteen))
    with pytest.raises(
        RuntimeError,
        match=r"candidates_checked=6, empty_up=6, empty_down=6, "
              r"checked_5m=3, checked_15m=3",
    ):
        await run_live_source_smoke(
            now=now,
            gamma_client=gamma,
            price_client=prices,
            bybit_client=FakeBybitClient(),
            coinbase_client=FakeCoinbaseClient(),
        )
    assert len(prices.calls) == 12
    assert five[3] not in gamma.calls
    assert fifteen[3] not in gamma.calls


@pytest.mark.asyncio
async def test_historical_smoke_does_not_mask_real_price_API_errors() -> None:
    now = datetime(2026, 8, 24, 22, 47, tzinfo=UTC)
    gamma = MultiClosedGammaClient(set(_recent_slugs(now, 5, 2)))
    prices = SelectivePriceClient(set(), error=True)
    with pytest.raises(RuntimeError, match="real CLOB price API failure"):
        await run_live_source_smoke(
            now=now,
            gamma_client=gamma,
            price_client=prices,
            bybit_client=FakeBybitClient(),
            coinbase_client=FakeCoinbaseClient(),
        )
    assert len(prices.calls) == 1
