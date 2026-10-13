"""Actual LOCAL repository insert counts, distinct from attempted B1 events."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine.url import make_url

from bp_engine.recorder.models import RawEvent
from bp_engine.recorder.stage_b1_insert_attribution import (
    _local_test_engine_only,
    insert_attributed_offline,
)
from bp_engine.storage.recorder import RecorderRepository
from bp_engine.storage.schema import metadata, raw_market_events

WHEN = datetime(2026, 10, 13, 12, tzinfo=UTC)


def _event(
    kind: str, sequence: int, *, source: str = "bybit", stream: str = "linear"
) -> RawEvent:
    return RawEvent.build(
        source=source,
        stream=stream,
        instrument="BTCUSDT",
        event_type=kind,
        source_timestamp=WHEN,
        received_at=WHEN,
        sequence=sequence,
        payload={"seq": sequence, "category": kind, "secret": "NOT_EXPORTED"},
    )


@pytest.fixture()
def db_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    yield engine
    engine.dispose()


def _count(engine) -> int:
    with engine.connect() as connection:
        result = connection.execute(
            select(func.count()).select_from(raw_market_events)
        ).scalar_one()
        return int(result)


def test_real_local_insert_count_by_class_with_in_batch_and_existing_duplicates(
    db_engine,
) -> None:
    trade = _event("trade", 1)
    book = _event("orderbook_delta", 2)
    ticker = _event("ticker", 3)
    other_source = _event("trade", 4, source="coinbase", stream="spot")
    liquid = _event("liquidation", 5)
    result = insert_attributed_offline(
        db_engine, [trade, trade, book, ticker, other_source, liquid]
    )
    assert result["status"] == "LOCAL_TEST_TRANSACTION_COMMITTED_NO_GO"
    assert result["attempted_by_class"] == {
        "trade": 2, "orderbook": 1, "liquidation": 1, "other": 0,
        "excluded_non_bulk": 2,
    }
    assert result["inserted_by_class"] == {
        "trade": 1, "orderbook": 1, "liquidation": 1, "other": 0,
        "excluded_non_bulk": 2,
    }
    assert result["deduplicated_by_class"]["trade"] == 1
    assert _count(db_engine) == 5
    assert result["real_local_db_commit_observed"] is True
    assert result["production_insert_attribution_proven"] is False
    assert result["production_execution_authorized"] is False
    assert "NOT_EXPORTED" not in json.dumps(result)
    assert "dedupe_key" not in json.dumps(result)

    repeat = insert_attributed_offline(db_engine, [trade, book, liquid])
    assert repeat["inserted_by_class"]["trade"] == 0
    assert repeat["inserted_by_class"]["orderbook"] == 0
    assert repeat["inserted_by_class"]["liquidation"] == 0
    assert repeat["deduplicated_by_class"]["trade"] == 1
    assert _count(db_engine) == 5


def test_failed_second_group_rolls_back_first_group_and_returns_no_counts(
    db_engine,
) -> None:
    class FailsOnBook(RecorderRepository):
        def insert_events(self, connection, events):
            if events and events[0].event_type == "orderbook_delta":
                raise RuntimeError("SECRET_WRITER_ERROR")
            return super().insert_events(connection, events)

    with pytest.raises(RuntimeError, match="SECRET_WRITER_ERROR"):
        insert_attributed_offline(
            db_engine,
            [_event("trade", 1), _event("orderbook_delta", 2)],
            repository=FailsOnBook(),
        )
    assert _count(db_engine) == 0


def test_cross_class_dedupe_collision_rejected_before_mutation(db_engine) -> None:
    trade = _event("trade", 1)
    book = _event("orderbook_delta", 2).model_copy(
        update={"dedupe_key": trade.dedupe_key}
    )
    with pytest.raises(ValueError, match="cross-category"):
        insert_attributed_offline(db_engine, [trade, book])
    assert _count(db_engine) == 0


def test_non_bulk_ticker_alone_is_inserted_but_never_claimed_as_bulk(db_engine) -> None:
    result = insert_attributed_offline(db_engine, [_event("ticker", 1)])
    assert result["inserted_by_class"]["trade"] == 0
    assert result["inserted_by_class"]["excluded_non_bulk"] == 1
    assert _count(db_engine) == 1


@pytest.mark.parametrize("bad", [[], [_event("trade", 1)] * 65, ("invalid",)])
def test_batch_limits_and_types_reject_before_mutation(db_engine, bad) -> None:
    with pytest.raises(ValueError):
        insert_attributed_offline(db_engine, bad)
    assert _count(db_engine) == 0


def test_nonlocal_or_file_database_rejected_without_connection(tmp_path) -> None:
    file_db = create_engine(f"sqlite:///{tmp_path / 'not-allowed.db'}")
    try:
        with pytest.raises(ValueError, match="in-memory SQLite"):
            insert_attributed_offline(file_db, [_event("trade", 1)])
    finally:
        file_db.dispose()

    for url in (
        "postgresql://reader@example.invalid/prod",
        "postgresql://reader@localhost/production",
        "mysql://localhost/bp",
    ):
        simulated_engine = SimpleNamespace(url=make_url(url))
        with pytest.raises(ValueError, match="B1"):
            _local_test_engine_only(simulated_engine)
