from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from bp_engine.storage.partitioned_raw import (
    RawStorageMode,
    ensure_partitioned_raw_storage,
    list_raw_partitions,
    raw_storage_mode,
)
from sqlalchemy import create_engine, insert, text

from bp_engine.storage.schema import raw_market_events

DATABASE_URL = os.getenv("BP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BP_TEST_DATABASE_URL is required for PostgreSQL partition coverage",
)


@pytest.fixture()
def engine():
    assert DATABASE_URL is not None
    value = create_engine(DATABASE_URL, pool_pre_ping=True)

    def reset() -> None:
        with value.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS raw_market_events CASCADE"))
            connection.execute(text("DROP TABLE IF EXISTS raw_market_events_legacy CASCADE"))
            connection.execute(text("DROP TABLE IF EXISTS raw_event_dedupe CASCADE"))
            connection.execute(text("DROP SEQUENCE IF EXISTS raw_market_events_id_seq_v2 CASCADE"))

    reset()
    raw_market_events.create(value)
    try:
        yield value
    finally:
        reset()
        raw_market_events.create(value)
        value.dispose()


def _is_partitioned(connection, table_name: str) -> bool:
    return bool(
        connection.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_partitioned_table AS p
                    JOIN pg_class AS c ON c.oid = p.partrelid
                    WHERE c.relname = :table_name
                )
                """
            ),
            {"table_name": table_name},
        ).scalar_one()
    )


def test_empty_legacy_table_initializes_hourly_raw_and_hash_dedupe_partitions(engine) -> None:
    now = datetime(2026, 9, 4, 11, 15, tzinfo=UTC)

    result = ensure_partitioned_raw_storage(
        engine,
        now=now,
        migrate_existing=False,
    )

    assert result.mode is RawStorageMode.PARTITIONED
    assert result.migrated_rows == 0
    assert result.rollback_table is None

    with engine.connect() as connection:
        assert raw_storage_mode(connection) is RawStorageMode.PARTITIONED
        assert _is_partitioned(connection, "raw_market_events")
        assert _is_partitioned(connection, "raw_event_dedupe")

        dedupe_children = connection.execute(
            text(
                """
                SELECT child.relname
                FROM pg_inherits
                JOIN pg_class AS parent ON parent.oid = pg_inherits.inhparent
                JOIN pg_class AS child ON child.oid = pg_inherits.inhrelid
                WHERE parent.relname = 'raw_event_dedupe'
                ORDER BY child.relname
                """
            )
        ).scalars().all()

        raw_partition_key = connection.execute(
            text(
                """
                SELECT pg_get_partkeydef(c.oid)
                FROM pg_class AS c
                WHERE c.relname = 'raw_market_events'
                """
            )
        ).scalar_one()

        default_children = connection.execute(
            text(
                """
                SELECT count(*)
                FROM pg_inherits
                JOIN pg_class AS parent ON parent.oid = pg_inherits.inhparent
                JOIN pg_class AS child ON child.oid = pg_inherits.inhrelid
                WHERE parent.relname = 'raw_market_events'
                  AND pg_get_expr(child.relpartbound, child.oid) = 'DEFAULT'
                """
            )
        ).scalar_one()

    assert dedupe_children == [f"raw_event_dedupe_h{index:02d}" for index in range(16)]
    assert raw_partition_key == "RANGE (received_at)"
    assert default_children == 0

    partitions = list_raw_partitions(engine)
    assert [(item.start_at, item.end_at) for item in partitions] == [
        (
            datetime(2026, 9, 4, 11, tzinfo=UTC),
            datetime(2026, 9, 4, 12, tzinfo=UTC),
        ),
        (
            datetime(2026, 9, 4, 12, tzinfo=UTC),
            datetime(2026, 9, 4, 13, tzinfo=UTC),
        ),
        (
            datetime(2026, 9, 4, 13, tzinfo=UTC),
            datetime(2026, 9, 4, 14, tzinfo=UTC),
        ),
    ]


def test_nonempty_legacy_table_requires_explicit_migration(engine) -> None:
    received_at = datetime(2026, 9, 3, 23, 59, tzinfo=UTC)
    with engine.begin() as connection:
        connection.execute(
            insert(raw_market_events).values(
                id=41,
                source="coinbase",
                stream="spot",
                instrument="BTC-USD",
                event_type="ticker",
                source_timestamp=received_at,
                received_at=received_at,
                sequence="41",
                market_id=None,
                asset_id=None,
                payload={"price": "60000"},
                dedupe_key="sha256:" + "1" * 64,
            )
        )

    result = ensure_partitioned_raw_storage(
        engine,
        now=datetime(2026, 9, 4, 0, 5, tzinfo=UTC),
        migrate_existing=False,
    )

    assert result.mode is RawStorageMode.LEGACY
    assert result.migrated_rows == 0
    with engine.connect() as connection:
        assert raw_storage_mode(connection) is RawStorageMode.LEGACY
        assert not _is_partitioned(connection, "raw_market_events")


def test_explicit_migration_preserves_rows_ids_dedupe_and_feed_ranges(engine) -> None:
    base = datetime(2026, 9, 3, 22, 5, tzinfo=UTC)
    rows = [
        {
            "id": 101,
            "source": "bybit",
            "stream": "linear",
            "instrument": "BTCUSDT",
            "event_type": "ticker",
            "source_timestamp": base,
            "received_at": base,
            "sequence": "101",
            "market_id": None,
            "asset_id": None,
            "payload": {"n": 101},
            "dedupe_key": "sha256:" + "a" * 64,
        },
        {
            "id": 104,
            "source": "polymarket",
            "stream": "market",
            "instrument": "condition",
            "event_type": "book",
            "source_timestamp": base + timedelta(hours=1),
            "received_at": base + timedelta(hours=1),
            "sequence": "104",
            "market_id": "condition",
            "asset_id": "token",
            "payload": {"n": 104},
            "dedupe_key": "sha256:" + "b" * 64,
        },
        {
            "id": 109,
            "source": "bybit",
            "stream": "linear",
            "instrument": "BTCUSDT",
            "event_type": "trade",
            "source_timestamp": base + timedelta(hours=2, minutes=50),
            "received_at": base + timedelta(hours=2, minutes=50),
            "sequence": "109",
            "market_id": None,
            "asset_id": None,
            "payload": {"n": 109},
            "dedupe_key": "sha256:" + "c" * 64,
        },
    ]
    with engine.begin() as connection:
        connection.execute(insert(raw_market_events), rows)

    result = ensure_partitioned_raw_storage(
        engine,
        now=datetime(2026, 9, 4, 1, 10, tzinfo=UTC),
        migrate_existing=True,
    )

    assert result.mode is RawStorageMode.PARTITIONED
    assert result.migrated_rows == 3
    assert result.rollback_table == "raw_market_events_legacy"

    with engine.connect() as connection:
        actual = connection.execute(
            text(
                """
                SELECT count(*), min(id), max(id), min(received_at), max(received_at)
                FROM raw_market_events
                """
            )
        ).one()
        legacy = connection.execute(
            text(
                """
                SELECT count(*), min(id), max(id), min(received_at), max(received_at)
                FROM raw_market_events_legacy
                """
            )
        ).one()
        dedupe = connection.execute(
            text(
                """
                SELECT count(*), min(id), max(id)
                FROM raw_event_dedupe
                """
            )
        ).one()
        feeds = connection.execute(
            text(
                """
                SELECT source, stream, count(*), min(id), max(id)
                FROM raw_market_events
                GROUP BY source, stream
                ORDER BY source, stream
                """
            )
        ).all()
        next_id = connection.execute(
            text("SELECT nextval('raw_market_events_id_seq_v2')")
        ).scalar_one()

    assert actual == legacy
    assert actual[0:3] == (3, 101, 109)
    assert dedupe == (3, 101, 109)
    assert feeds == [
        ("bybit", "linear", 2, 101, 109),
        ("polymarket", "market", 1, 104, 104),
    ]
    assert next_id > 109
