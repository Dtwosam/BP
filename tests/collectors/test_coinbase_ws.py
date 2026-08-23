import json
from datetime import UTC, datetime
from pathlib import Path

from bp_engine.collectors.coinbase_ws import build_coinbase_subscriptions, parse_coinbase_message

FIXTURES = Path(__file__).parents[1] / "fixtures" / "coinbase"


def load(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text())


def test_build_subscriptions_include_ticker_trades_and_heartbeats() -> None:
    assert build_coinbase_subscriptions(["BTC-USD"]) == [
        {"type": "subscribe", "product_ids": ["BTC-USD"], "channel": "ticker"},
        {"type": "subscribe", "product_ids": ["BTC-USD"], "channel": "market_trades"},
        {"type": "subscribe", "channel": "heartbeats"},
    ]


def test_parse_level2_snapshot_preserves_updates_and_sequence() -> None:
    event = parse_coinbase_message(
        load("level2_snapshot.json"),
        received_at=datetime(2026, 8, 20, 22, 20, tzinfo=UTC),
    )[0]

    assert event.source == "coinbase"
    assert event.stream == "spot"
    assert event.instrument == "BTC-USD"
    assert event.event_type == "level2_snapshot"
    assert event.sequence == "42"
    assert event.payload["events"][0]["updates"][0]["side"] == "bid"


def test_parse_ticker_preserves_top_of_book() -> None:
    event = parse_coinbase_message(
        load("ticker.json"),
        received_at=datetime(2026, 8, 20, 22, 20, tzinfo=UTC),
    )[0]

    assert event.event_type == "ticker_snapshot"
    assert event.instrument == "BTC-USD"
    assert event.sequence == "44"
    ticker = event.payload["events"][0]["tickers"][0]
    assert ticker["best_bid"] == "72690.01"
    assert ticker["best_ask"] == "72690.02"


def test_parse_market_trades_keeps_trade_batch() -> None:
    event = parse_coinbase_message(
        load("market_trades.json"),
        received_at=datetime(2026, 8, 20, 22, 20, tzinfo=UTC),
    )[0]

    assert event.event_type == "market_trades_update"
    assert event.instrument == "BTC-USD"
    assert event.sequence == "43"
    assert event.payload["events"][0]["trades"][0]["side"] == "BUY"


def test_heartbeat_is_control_traffic_not_raw_market_event() -> None:
    assert parse_coinbase_message(
        load("heartbeat.json"),
        received_at=datetime(2026, 8, 20, 22, 20, tzinfo=UTC),
    ) == []
