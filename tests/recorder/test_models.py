from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from bp_engine.recorder.models import RawEvent, build_dedupe_key, canonical_payload_hash


def test_canonical_payload_hash_is_stable_across_json_key_order() -> None:
    first = {"b": 2, "a": {"y": [2, 1], "x": True}}
    second = {"a": {"x": True, "y": [2, 1]}, "b": 2}

    assert canonical_payload_hash(first) == canonical_payload_hash(second)


def test_raw_event_normalizes_aware_timestamps_to_utc() -> None:
    source_time = datetime(2026, 8, 20, 23, 30, tzinfo=timezone(timedelta(hours=2)))
    received_at = datetime(2026, 8, 20, 21, 30, 1, tzinfo=UTC)
    payload = {"event_type": "book", "timestamp": "1787261400000"}

    event = RawEvent.build(
        source="polymarket",
        stream="market",
        instrument="btc-updown-5m",
        event_type="book",
        source_timestamp=source_time,
        received_at=received_at,
        sequence="abc",
        market_id="0xmarket",
        asset_id="123",
        payload=payload,
    )

    assert event.source_timestamp == datetime(2026, 8, 20, 21, 30, tzinfo=UTC)
    assert event.received_at.tzinfo is UTC
    assert event.dedupe_key.startswith("sha256:")


def test_raw_event_rejects_naive_timestamps() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        RawEvent.build(
            source="bybit",
            stream="spot",
            instrument="BTCUSDT",
            event_type="trade",
            source_timestamp=datetime(2026, 8, 20, 21, 30),
            received_at=datetime(2026, 8, 20, 21, 30, 1, tzinfo=UTC),
            payload={"topic": "publicTrade.BTCUSDT"},
        )


def test_dedupe_key_changes_when_event_identity_changes() -> None:
    payload = {"topic": "orderbook.50.BTCUSDT", "data": {"u": 42}}
    first = build_dedupe_key(
        source="bybit",
        stream="spot",
        event_type="orderbook",
        source_timestamp=datetime(2026, 8, 20, 21, 30, tzinfo=UTC),
        sequence="42",
        payload=payload,
    )
    second = build_dedupe_key(
        source="bybit",
        stream="spot",
        event_type="orderbook",
        source_timestamp=datetime(2026, 8, 20, 21, 30, 0, 1000, tzinfo=UTC),
        sequence="43",
        payload=payload,
    )

    assert first != second
