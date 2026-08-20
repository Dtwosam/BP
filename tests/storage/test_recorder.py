from datetime import UTC, datetime

from sqlalchemy import create_engine, select

from bp_engine.recorder.models import FeedIncident, RawEvent
from bp_engine.storage.recorder import RecorderRepository
from bp_engine.storage.schema import feed_incidents, metadata, raw_market_events


def make_event(sequence: str = "42") -> RawEvent:
    return RawEvent.build(
        source="bybit",
        stream="spot",
        instrument="BTCUSDT",
        event_type="orderbook",
        source_timestamp=datetime(2026, 8, 20, 21, 30, tzinfo=UTC),
        received_at=datetime(2026, 8, 20, 21, 30, 0, 1000, tzinfo=UTC),
        sequence=sequence,
        payload={"topic": "orderbook.50.BTCUSDT", "data": {"u": int(sequence)}},
    )


def test_insert_events_is_idempotent_and_preserves_payload() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    repository = RecorderRepository()
    event = make_event()

    with engine.begin() as connection:
        assert repository.insert_events(connection, [event]) == 1
        assert repository.insert_events(connection, [event]) == 0
        row = connection.execute(select(raw_market_events)).mappings().one()

    assert row["source"] == "bybit"
    assert row["sequence"] == "42"
    assert row["payload"] == {"topic": "orderbook.50.BTCUSDT", "data": {"u": 42}}
    assert row["dedupe_key"] == event.dedupe_key


def test_insert_events_accepts_multiple_distinct_events_in_one_batch() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    repository = RecorderRepository()

    with engine.begin() as connection:
        inserted = repository.insert_events(connection, [make_event("42"), make_event("43")])
        rows = connection.execute(select(raw_market_events)).mappings().all()

    assert inserted == 2
    assert {row["sequence"] for row in rows} == {"42", "43"}


def test_record_incident_persists_structured_details() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    repository = RecorderRepository()
    incident = FeedIncident(
        source="polymarket",
        stream="market",
        incident_type="disconnect",
        observed_at=datetime(2026, 8, 20, 21, 31, tzinfo=UTC),
        details={"reason": "socket closed", "attempt": 2},
    )

    with engine.begin() as connection:
        repository.record_incident(connection, incident)
        row = connection.execute(select(feed_incidents)).mappings().one()

    assert row["incident_type"] == "disconnect"
    assert row["details"] == {"reason": "socket closed", "attempt": 2}
