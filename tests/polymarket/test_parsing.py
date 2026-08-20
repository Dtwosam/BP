import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bp_engine.polymarket.parsing import GammaMarketError, parse_gamma_market, parse_horizon_slug

FIXTURES = Path(__file__).parents[1] / "fixtures" / "polymarket"


def load_fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text())


def test_parse_horizon_slug_uses_epoch_as_market_window_start() -> None:
    seconds, start = parse_horizon_slug("btc-updown-5m-1787198400")

    assert seconds == 300
    assert start == datetime(2026, 8, 20, 4, 0, tzinfo=UTC)


def test_parse_5m_market_maps_tokens_by_outcome_label_not_array_position() -> None:
    market = parse_gamma_market(load_fixture("btc_updown_5m_gamma.json"))

    assert market.horizon_seconds == 300
    assert market.window_start_at == datetime(2026, 8, 20, 4, 0, tzinfo=UTC)
    assert market.window_end_at == datetime(2026, 8, 20, 4, 5, tzinfo=UTC)
    assert market.up_token_id == "up-token-5m"
    assert market.down_token_id == "down-token-5m"
    assert market.resolved_outcome == "Up"
    assert market.rules_hash.startswith("sha256:")


def test_parse_15m_market_preserves_exact_resolution_metadata() -> None:
    payload = load_fixture("btc_updown_15m_gamma.json")
    market = parse_gamma_market(payload)

    assert market.horizon_seconds == 900
    assert market.resolution_source == payload["resolutionSource"]
    assert market.rules_text == payload["description"]
    assert market.active is True
    assert market.closed is False
    assert market.accepting_orders is True
    assert market.resolved_outcome is None


def test_parser_rejects_mismatched_outcome_and_token_arrays() -> None:
    payload = copy.deepcopy(load_fixture("btc_updown_5m_gamma.json"))
    payload["clobTokenIds"] = '["only-one-token"]'

    with pytest.raises(GammaMarketError, match="outcomes and clobTokenIds"):
        parse_gamma_market(payload)
