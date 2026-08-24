from datetime import UTC, datetime, timedelta
from decimal import Decimal

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


@pytest.mark.asyncio
async def test_live_source_smoke_returns_sanitized_nonempty_counts() -> None:
    now = datetime(2026, 8, 24, 22, 47, tzinfo=UTC)
    expected_epoch = int(datetime(2026, 8, 24, 22, 35, tzinfo=UTC).timestamp())
    gamma = FakeGammaClient(f"btc-updown-5m-{expected_epoch}")

    report = await run_live_source_smoke(
        now=now,
        gamma_client=gamma,
        price_client=FakePriceClient(),
        bybit_client=FakeBybitClient(),
        coinbase_client=FakeCoinbaseClient(),
    )

    assert report["status"] == "ok"
    assert report["polymarket"]["up_price_points"] == 1
    assert report["polymarket"]["down_price_points"] == 1
    assert report["bybit"]["spot_candles"] == 1
    assert report["bybit"]["linear_candles"] == 1
    assert report["coinbase"]["spot_candles"] == 1
    assert "asset-up" not in str(report)
    assert "asset-down" not in str(report)
