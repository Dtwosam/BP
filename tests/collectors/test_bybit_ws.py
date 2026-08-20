import json
from datetime import UTC, datetime
from pathlib import Path

from bp_engine.collectors.bybit_ws import build_bybit_subscription, parse_bybit_message

FIXTURES = Path(__file__).parents[1] / "fixtures" / "bybit"


def load(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text())


def test_build_subscription_deduplicates_topics() -> None:
    topics = ["publicTrade.BTCUSDT", "orderbook.50.BTCUSDT", "publicTrade.BTCUSDT"]
    assert build_bybit_subscription(topics) == {
        "op": "subscribe",
        "args": ["orderbook.50.BTCUSDT", "publicTrade.BTCUSDT"],
    }


def test_parse_orderbook_uses_matching_engine_timestamp_and_cross_sequence() -> None:
    received_at = datetime(2026, 8, 20, 21, 30, tzinfo=UTC)

    event = parse_bybit_message(
        load("orderbook_snapshot.json"), venue="spot", received_at=received_at
    )[0]

    assert event.event_type == "orderbook_snapshot"
    assert event.instrument == "BTCUSDT"
    assert event.sequence == "66544703342"
    assert event.source_timestamp == datetime.fromtimestamp(1687940967.464, tz=UTC)
    assert event.payload["data"]["u"] == 177400507


def test_parse_orderbook_delta_keeps_zero_size_deletion() -> None:
    received_at = datetime(2026, 8, 20, 21, 30, tzinfo=UTC)

    event = parse_bybit_message(
        load("orderbook_delta.json"), venue="spot", received_at=received_at
    )[0]

    assert event.event_type == "orderbook_delta"
    assert event.payload["data"]["b"] == [["30240.00", "0"]]


def test_parse_public_trade_keeps_full_trade_batch_and_server_timestamp() -> None:
    received_at = datetime(2026, 8, 20, 21, 30, tzinfo=UTC)

    event = parse_bybit_message(load("public_trade.json"), venue="spot", received_at=received_at)[0]

    assert event.event_type == "trade"
    assert event.sequence == "1783284617"
    assert event.source_timestamp == datetime.fromtimestamp(1672304486.868, tz=UTC)
    assert event.payload["data"][0]["S"] == "Buy"


def test_parse_linear_ticker_preserves_open_interest_and_funding() -> None:
    received_at = datetime(2026, 8, 20, 21, 30, tzinfo=UTC)

    event = parse_bybit_message(
        load("linear_ticker.json"), venue="linear", received_at=received_at
    )[0]

    assert event.event_type == "ticker"
    assert event.sequence == "9532239429"
    assert event.payload["data"]["openInterest"] == "492373.72"
    assert event.payload["data"]["fundingRate"] == "-0.005"


def test_parse_liquidation_keeps_batch_payload() -> None:
    received_at = datetime(2026, 8, 20, 21, 30, tzinfo=UTC)

    event = parse_bybit_message(
        load("liquidation.json"), venue="linear", received_at=received_at
    )[0]

    assert event.event_type == "liquidation"
    assert event.payload["data"][0]["S"] == "Sell"


def test_subscription_ack_is_not_recorded() -> None:
    received_at = datetime(2026, 8, 20, 21, 30, tzinfo=UTC)
    ack = {"success": True, "ret_msg": "", "op": "subscribe", "conn_id": "abc"}

    assert parse_bybit_message(ack, venue="spot", received_at=received_at) == []
