from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, delete, select

from bp_engine.execution.book import BookLevel, PolymarketBookReplayReader
from bp_engine.recorder.models import RawEvent
from bp_engine.storage.recorder import RecorderRepository
from bp_engine.storage.schema import metadata, raw_market_events

DATABASE_URL = os.getenv("BP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BP_TEST_DATABASE_URL is required for PostgreSQL integration coverage",
)


def _event(
    *,
    event_type: str,
    received_at: datetime,
    payload: dict[str, object],
    asset_id: str | None,
) -> RawEvent:
    return RawEvent.build(
        source="polymarket",
        stream="market",
        instrument="phase12-book-condition",
        event_type=event_type,
        source_timestamp=None,
        received_at=received_at,
        market_id="phase12-book-condition",
        asset_id=asset_id,
        payload=payload,
    )


def test_postgres_book_reader_is_causal_and_requires_a_full_anchor() -> None:
    assert DATABASE_URL is not None
    engine = create_engine(DATABASE_URL)
    metadata.create_all(engine)
    reader = PolymarketBookReplayReader()
    recorder = RecorderRepository()
    base = datetime(2026, 8, 28, 16, 35, tzinfo=UTC)

    anchor = _event(
        event_type="book",
        received_at=base,
        asset_id="up-token",
        payload={
            "event_type": "book",
            "market": "phase12-book-condition",
            "asset_id": "up-token",
            "bids": [{"price": "0.54", "size": "10"}],
            "asks": [
                {"price": "0.56", "size": "2"},
                {"price": "0.57", "size": "4"},
            ],
        },
    )
    before_cutoff = _event(
        event_type="price_change",
        received_at=base + timedelta(milliseconds=100),
        asset_id=None,
        payload={
            "event_type": "price_change",
            "market": "phase12-book-condition",
            "price_changes": [
                {
                    "asset_id": "up-token",
                    "side": "SELL",
                    "price": "0.56",
                    "size": "1.5",
                }
            ],
        },
    )
    after_cutoff = _event(
        event_type="price_change",
        received_at=base + timedelta(milliseconds=600),
        asset_id=None,
        payload={
            "event_type": "price_change",
            "market": "phase12-book-condition",
            "price_changes": [
                {
                    "asset_id": "up-token",
                    "side": "SELL",
                    "price": "0.55",
                    "size": "20",
                }
            ],
        },
    )
    events = (anchor, before_cutoff, after_cutoff)
    keys = tuple(event.dedupe_key for event in events)

    with engine.begin() as connection:
        connection.execute(
            delete(raw_market_events).where(raw_market_events.c.dedupe_key.in_(keys))
        )
        recorder.insert_events(connection, events)
        rows = connection.execute(
            select(raw_market_events.c.id, raw_market_events.c.dedupe_key).where(
                raw_market_events.c.dedupe_key.in_(keys)
            )
        ).all()
        assert len(rows) == 3
        ids = {dedupe_key: row_id for row_id, dedupe_key in rows}

        replayed = reader.book_at(
            connection,
            condition_id="phase12-book-condition",
            asset_id="up-token",
            observed_at=base + timedelta(milliseconds=500),
        )
        missing = reader.book_at(
            connection,
            condition_id="phase12-book-condition",
            asset_id="down-token",
            observed_at=base + timedelta(milliseconds=500),
        )

        connection.execute(
            delete(raw_market_events).where(raw_market_events.c.dedupe_key.in_(keys))
        )

    assert replayed is not None
    assert replayed.asks == (
        BookLevel(price=Decimal("0.56"), size=Decimal("1.5")),
        BookLevel(price=Decimal("0.57"), size=Decimal("4")),
    )
    assert replayed.anchor_event_id == ids[anchor.dedupe_key]
    assert replayed.anchor_dedupe_key == anchor.dedupe_key
    assert replayed.applied_event_ids == (ids[before_cutoff.dedupe_key],)
    assert replayed.applied_dedupe_keys == (before_cutoff.dedupe_key,)
    assert after_cutoff.dedupe_key not in replayed.applied_dedupe_keys
    assert missing is None
