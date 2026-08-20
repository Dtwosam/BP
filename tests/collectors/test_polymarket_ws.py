import json
from datetime import UTC, datetime
from pathlib import Path

from bp_engine.collectors.polymarket_ws import (
    build_market_subscription,
    build_subscription_update,
    parse_polymarket_message,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "polymarket_ws"


def load(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text())


def test_subscription_builders_deduplicate_asset_ids() -> None:
    assert build_market_subscription(["b", "a", "a"]) == {
        "assets_ids": ["a", "b"],
        "type": "market",
    }
    assert build_subscription_update("unsubscribe", ["b", "b"]) == {
        "operation": "unsubscribe",
        "assets_ids": ["b"],
    }


def test_parse_book_preserves_market_asset_and_source_timestamp() -> None:
    received_at = datetime(2026, 8, 20, 21, 30, tzinfo=UTC)

    events = parse_polymarket_message(load("book.json"), received_at=received_at)

    assert len(events) == 1
    event = events[0]
    assert event.source == "polymarket"
    assert event.stream == "market"
    assert event.event_type == "book"
    assert event.market_id == "0xbd31dc8a20211944f6b70f31557f1001557b59905b7738480ca09bd4532f84af"
    assert event.asset_id is not None
    assert event.source_timestamp == datetime.fromtimestamp(1757908892.351, tz=UTC)
    assert event.payload["bids"][0] == {"price": "0.48", "size": "30"}


def test_parse_price_change_keeps_message_level_payload() -> None:
    received_at = datetime(2026, 8, 20, 21, 30, tzinfo=UTC)

    event = parse_polymarket_message(load("price_change.json"), received_at=received_at)[0]

    assert event.event_type == "price_change"
    assert event.asset_id is None
    assert event.payload["price_changes"][0]["side"] == "BUY"


def test_parse_last_trade_uses_asset_id() -> None:
    received_at = datetime(2026, 8, 20, 21, 30, tzinfo=UTC)

    event = parse_polymarket_message(load("last_trade_price.json"), received_at=received_at)[0]

    assert event.event_type == "last_trade_price"
    assert event.asset_id == (
        "114122071509644379678018727908709560226618148003371446110114509806601493071694"
    )
    assert event.payload["price"] == "0.456"


def test_control_pong_is_not_recorded_as_market_data() -> None:
    received_at = datetime(2026, 8, 20, 21, 30, tzinfo=UTC)

    assert parse_polymarket_message("PONG", received_at=received_at) == []
