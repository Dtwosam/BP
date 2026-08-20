import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bp_engine.polymarket.discovery import build_candidate_slugs, discover_btc_markets

FIXTURES = Path(__file__).parents[1] / "fixtures" / "polymarket"


def load_fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text())


def test_candidate_slugs_floor_each_horizon_independently_in_utc() -> None:
    now = datetime(2026, 8, 20, 4, 7, 31, tzinfo=UTC)

    slugs = build_candidate_slugs(now, horizons=("5m", "15m"), offsets=(0,))

    assert slugs == [
        "btc-updown-5m-1787198700",
        "btc-updown-15m-1787198400",
    ]


def test_candidate_slugs_include_adjacent_windows_without_hardcoding_10m() -> None:
    now = datetime(2026, 8, 20, 4, 7, tzinfo=UTC)

    slugs = build_candidate_slugs(now, horizons=("5m",), offsets=(-1, 0, 1))

    assert slugs == [
        "btc-updown-5m-1787198400",
        "btc-updown-5m-1787198700",
        "btc-updown-5m-1787199000",
    ]
    assert all("10m" not in slug for slug in slugs)


class FakeGammaClient:
    def __init__(self, payloads: dict[str, dict[str, object]]) -> None:
        self.payloads = payloads
        self.requested: list[str] = []

    async def get_market_by_slug(self, slug: str) -> dict[str, object] | None:
        self.requested.append(slug)
        return self.payloads.get(slug)


@pytest.mark.asyncio
async def test_discovery_parses_and_deduplicates_markets() -> None:
    now = datetime(2026, 8, 20, 21, 16, tzinfo=UTC)
    payload = load_fixture("btc_updown_5m_gamma.json")
    client = FakeGammaClient(
        {
            "btc-updown-5m-1787260500": payload,
            "btc-updown-5m-1787260800": payload,
        }
    )

    markets = await discover_btc_markets(client, now, horizons=("5m",), offsets=(0, 1))

    assert client.requested == [
        "btc-updown-5m-1787260500",
        "btc-updown-5m-1787260800",
    ]
    assert len(markets) == 1
    assert markets[0].condition_id == (
        "0x3b04d4ca0dd684c6571e41b401fa68f91a1ef3dbfef6a8204ab721b2e0144d53"
    )
