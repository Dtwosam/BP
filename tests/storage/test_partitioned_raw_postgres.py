from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from time import monotonic, sleep

import pytest
from sqlalchemy import create_engine, event, insert, text
from sqlalchemy.exc import DBAPIError

from bp_engine.config import Settings
from bp_engine.recorder.models import RawEvent
from bp_engine.recorder.stage_b1_insert_attribution import (
    insert_attributed_offline,
    insert_same_batch_attributed_offline,
)
from bp_engine.recorder.state import MarketStateSnapshot
from bp_engine.recorder.writer import BatchWriter, EventBuffer
from bp_engine.storage.maintenance import (
    ArchiveVerificationError,
    archive_interval,
    build_composite_storage_health,
    prune_expired_archives,
    retire_verified_partition,
)
from bp_engine.storage.partitioned_raw import (
    RawStorageMode,
    drop_raw_partition,
    ensure_hour_partitions,
    ensure_partitioned_raw_storage,
    list_raw_partitions,
    list_raw_retirement_candidates,
    raw_storage_mode,
    rollback_partitioned_raw_storage,
)
from bp_engine.storage.recorder import RecorderRepository
from bp_engine.storage.schema import (
    market_state_1s,
    raw_market_events,
    storage_maintenance_runs,
)
from bp_engine.v4_paper.source_time_features import V4SourceTimeReader
from scripts.benchmark_stage_b1_postcommit_local import (
    compare_local_postcommit_overhead,
)
from scripts.deploy.migrate_partitioned_raw_storage import _exact_raw_parity
from scripts.report_v4_linear_trade_storage_b0 import build_report as build_linear_b0_report

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
            state_exists = connection.execute(
                text("SELECT to_regclass('market_state_1s')")
            ).scalar_one()
            if state_exists is not None:
                connection.execute(text("DELETE FROM market_state_1s"))
            maintenance_exists = connection.execute(
                text("SELECT to_regclass('storage_maintenance_runs')")
            ).scalar_one()
            if maintenance_exists is not None:
                connection.execute(text("DELETE FROM storage_maintenance_runs"))

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



def test_new_hour_partition_gets_v4_source_lookup_index(engine) -> None:
    now = datetime(2026, 9, 4, 11, 15, tzinfo=UTC)
    ensure_partitioned_raw_storage(
        engine,
        now=now,
        migrate_existing=False,
    )

    with engine.connect() as connection:
        indexdef = connection.execute(
            text(
                """
                SELECT indexdef
                FROM pg_indexes
                WHERE schemaname = current_schema()
                  AND tablename = 'raw_market_events_20260904_11'
                  AND indexname =
                      'raw_market_events_20260904_11_v4_source_lookup_idx'
                """
            )
        ).scalar_one()

    normalized = " ".join(indexdef.split())
    assert (
        "(source, stream, instrument, received_at DESC, id DESC)"
        in normalized
    )
    assert "WHERE (source_timestamp IS NOT NULL)" in normalized


def test_v4_reader_queries_attached_hour_child_directly(engine) -> None:
    requested = datetime(2026, 9, 4, 11, 15, tzinfo=UTC)
    ensure_partitioned_raw_storage(
        engine,
        now=requested,
        migrate_existing=False,
    )
    with engine.begin() as connection:
        connection.execute(
            insert(raw_market_events).values(
                id=910001,
                source="bybit",
                stream="spot",
                instrument="BTCUSDT",
                event_type="ticker",
                source_timestamp=requested - timedelta(seconds=0.2),
                received_at=requested - timedelta(seconds=0.1),
                sequence="v4-direct-child",
                market_id=None,
                asset_id=None,
                payload={"data": {"lastPrice": "50000"}},
                dedupe_key="v4-direct-child",
            )
        )

    statements: list[str] = []

    def capture(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        with engine.connect() as connection:
            observation, evidence = V4SourceTimeReader().latest_price(
                connection,
                source="bybit",
                stream="spot",
                instrument="BTCUSDT",
                requested_at=requested,
            )
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert observation is not None
    assert float(observation.price) == 50000.0
    assert len(evidence) == 1
    assert any(
        "FROM raw_market_events_20260904_11" in statement
        for statement in statements
    )



def test_v4_reader_cannot_see_eligible_uncommitted_bybit_linear_trade(engine) -> None:
    """Synthetic PostgreSQL MVCC: eligible received_at is not commit visibility."""
    requested = datetime(2026, 9, 4, 11, 15, tzinfo=UTC)
    ensure_partitioned_raw_storage(engine, now=requested, migrate_existing=False)

    # The writer owns a transaction separate from the decision reader.
    with engine.connect() as writer, engine.connect() as decision_reader:
        assert decision_reader.execute(text("SHOW transaction_isolation")).scalar_one() == (
            "read committed"
        )
        transaction = writer.begin()
        try:
            writer.execute(
                insert(raw_market_events).values(
                    id=910101,
                    source="bybit",
                    stream="linear",
                    instrument="BTCUSDT",
                    event_type="trade",
                    source_timestamp=requested - timedelta(seconds=0.2),
                    received_at=requested - timedelta(seconds=0.1),
                    sequence="linear-mvcc-uncommitted",
                    market_id=None,
                    asset_id=None,
                    payload={"data": [{"p": "50101"}]},
                    dedupe_key="linear-mvcc-uncommitted",
                )
            )
            # Its source/receipt times are already within the frozen cutoff,
            # but the row is invisible until the separate writer commits.
            before, before_evidence = V4SourceTimeReader().latest_price(
                decision_reader,
                source="bybit",
                stream="linear",
                instrument="BTCUSDT",
                requested_at=requested,
            )
            assert before is None
            assert before_evidence == ()

            transaction.commit()
            # READ COMMITTED takes a new statement snapshot in the same
            # decision_reader connection, so the same event is now visible.
            after, after_evidence = V4SourceTimeReader().latest_price(
                decision_reader,
                source="bybit",
                stream="linear",
                instrument="BTCUSDT",
                requested_at=requested,
            )
            assert after is not None
            assert float(after.price) == 50101.0
            assert len(after_evidence) == 1
            assert after_evidence[0].received_at < requested
            assert after_evidence[0].source_at < requested
        finally:
            if transaction.is_active:
                transaction.rollback()


def test_v4_reader_prefetch_keeps_precommit_snapshot_until_refreshed(engine) -> None:
    """The per-stage candidate cache is a statement-time view, not a live feed."""
    requested = datetime(2026, 9, 4, 11, 15, tzinfo=UTC)
    ensure_partitioned_raw_storage(engine, now=requested, migrate_existing=False)
    request = (("bybit", "linear", "BTCUSDT", requested, False),)

    with engine.connect() as writer, engine.connect() as decision_reader:
        transaction = writer.begin()
        try:
            writer.execute(
                insert(raw_market_events).values(
                    id=910102,
                    source="bybit",
                    stream="linear",
                    instrument="BTCUSDT",
                    event_type="ticker",
                    source_timestamp=requested - timedelta(seconds=0.2),
                    received_at=requested - timedelta(seconds=0.1),
                    sequence="linear-mvcc-cached",
                    market_id=None,
                    asset_id=None,
                    payload={"data": {"lastPrice": "50102"}},
                    dedupe_key="linear-mvcc-cached",
                )
            )
            reader = V4SourceTimeReader()
            reader.prefetch_candidate_rows(decision_reader, request)
            before, _ = reader.latest_price(
                decision_reader,
                source="bybit",
                stream="linear",
                instrument="BTCUSDT",
                requested_at=requested,
            )
            assert before is None
            transaction.commit()

            # An already prefetched (empty) candidate set remains empty
            # even when the PostgreSQL row has since committed.
            cached, cached_evidence = reader.latest_price(
                decision_reader,
                source="bybit",
                stream="linear",
                instrument="BTCUSDT",
                requested_at=requested,
            )
            assert cached is None
            assert cached_evidence == ()

            reader.prefetch_candidate_rows(decision_reader, request)
            refreshed, evidence = reader.latest_price(
                decision_reader,
                source="bybit",
                stream="linear",
                instrument="BTCUSDT",
                requested_at=requested,
            )
            assert refreshed is not None
            assert float(refreshed.price) == 50102.0
            assert len(evidence) == 1
        finally:
            if transaction.is_active:
                transaction.rollback()


def test_v4_reader_queries_both_children_across_hour_boundary(engine) -> None:
    requested = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
    ensure_partitioned_raw_storage(
        engine,
        now=requested - timedelta(minutes=45),
        migrate_existing=False,
    )
    with engine.begin() as connection:
        connection.execute(
            insert(raw_market_events),
            [
                {
                    "id": 910002,
                    "source": "bybit",
                    "stream": "spot",
                    "instrument": "BTCUSDT",
                    "event_type": "ticker",
                    "source_timestamp": requested - timedelta(seconds=0.4),
                    "received_at": requested - timedelta(seconds=0.2),
                    "sequence": "v4-hour-left",
                    "market_id": None,
                    "asset_id": None,
                    "payload": {"data": {"lastPrice": "50001"}},
                    "dedupe_key": "v4-hour-left",
                },
                {
                    "id": 910003,
                    "source": "bybit",
                    "stream": "spot",
                    "instrument": "BTCUSDT",
                    "event_type": "ticker",
                    "source_timestamp": requested,
                    "received_at": requested,
                    "sequence": "v4-hour-right",
                    "market_id": None,
                    "asset_id": None,
                    "payload": {"data": {"lastPrice": "50002"}},
                    "dedupe_key": "v4-hour-right",
                },
            ],
        )

    statements: list[str] = []

    def capture(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        with engine.connect() as connection:
            observation, _ = V4SourceTimeReader().latest_price(
                connection,
                source="bybit",
                stream="spot",
                instrument="BTCUSDT",
                requested_at=requested,
            )
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert observation is not None
    assert float(observation.price) == 50002.0
    assert any(
        "FROM raw_market_events_20260904_11" in statement
        for statement in statements
    )
    assert any(
        "FROM raw_market_events_20260904_12" in statement
        for statement in statements
    )


def test_v4_reader_treats_missing_hour_child_as_no_rows(engine) -> None:
    now = datetime(2026, 9, 4, 11, 15, tzinfo=UTC)
    ensure_partitioned_raw_storage(
        engine,
        now=now,
        migrate_existing=False,
    )

    with engine.connect() as connection:
        observation, evidence = V4SourceTimeReader().latest_price(
            connection,
            source="coinbase",
            stream="spot",
            instrument="BTC-USD",
            requested_at=now - timedelta(hours=2),
        )

    assert observation is None
    assert evidence == ()


def test_v4_reader_batches_prefetched_partition_candidates(engine) -> None:
    requested = datetime(2026, 9, 4, 11, 15, tzinfo=UTC)
    prior = requested - timedelta(minutes=5)
    ensure_partitioned_raw_storage(
        engine,
        now=requested,
        migrate_existing=False,
    )
    with engine.begin() as connection:
        connection.execute(
            insert(raw_market_events),
            [
                {
                    "id": 910004,
                    "source": "bybit",
                    "stream": "spot",
                    "instrument": "BTCUSDT",
                    "event_type": "ticker",
                    "source_timestamp": prior - timedelta(seconds=0.2),
                    "received_at": prior - timedelta(seconds=0.1),
                    "sequence": "v4-batch-prior",
                    "market_id": None,
                    "asset_id": None,
                    "payload": {"data": {"lastPrice": "50003"}},
                    "dedupe_key": "v4-batch-prior",
                },
                {
                    "id": 910005,
                    "source": "bybit",
                    "stream": "spot",
                    "instrument": "BTCUSDT",
                    "event_type": "ticker",
                    "source_timestamp": requested - timedelta(seconds=0.2),
                    "received_at": requested - timedelta(seconds=0.1),
                    "sequence": "v4-batch-current",
                    "market_id": None,
                    "asset_id": None,
                    "payload": {"data": {"lastPrice": "50004"}},
                    "dedupe_key": "v4-batch-current",
                },
            ],
        )

    statements: list[str] = []

    def capture(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        statements.append(statement)

    reader = V4SourceTimeReader()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        with engine.connect() as connection:
            reader.prefetch_candidate_rows(
                connection,
                (
                    (
                        "bybit",
                        "spot",
                        "BTCUSDT",
                        prior,
                        False,
                    ),
                    (
                        "bybit",
                        "spot",
                        "BTCUSDT",
                        requested,
                        False,
                    ),
                ),
            )
            statement_count_after_prefetch = len(statements)
            prior_observation, _ = reader.latest_price(
                connection,
                source="bybit",
                stream="spot",
                instrument="BTCUSDT",
                requested_at=prior,
            )
            current_observation, _ = reader.latest_price(
                connection,
                source="bybit",
                stream="spot",
                instrument="BTCUSDT",
                requested_at=requested,
            )
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert prior_observation is not None
    assert float(prior_observation.price) == 50003.0
    assert current_observation is not None
    assert float(current_observation.price) == 50004.0
    assert len(statements) == statement_count_after_prefetch

    prior_key = reader._candidate_cache_key(
        source="bybit",
        stream="spot",
        instrument="BTCUSDT",
        requested_at=prior,
        ticker_only=False,
    )
    current_key = reader._candidate_cache_key(
        source="bybit",
        stream="spot",
        instrument="BTCUSDT",
        requested_at=requested,
        ticker_only=False,
    )
    assert [
        int(row["id"])
        for row in reader._prefetched_candidate_rows[prior_key]
    ] == [910004]
    assert [
        int(row["id"])
        for row in reader._prefetched_candidate_rows[current_key]
    ] == [910005]

    candidate_statements = [
        statement
        for statement in statements
        if "CROSS JOIN LATERAL" in statement
        and "raw_market_events_20260904_11" in statement
    ]
    assert len(candidate_statements) == 1
    assert "WITH requests(" in candidate_statements[0]
    assert "VALUES" in candidate_statements[0]
    assert "UNION ALL" not in candidate_statements[0]


def test_v4_reader_lateral_prefetch_merges_cross_hour_candidates(engine) -> None:
    requested = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
    ensure_partitioned_raw_storage(
        engine,
        now=requested - timedelta(minutes=45),
        migrate_existing=False,
    )
    with engine.begin() as connection:
        connection.execute(
            insert(raw_market_events),
            [
                {
                    "id": 910006,
                    "source": "bybit",
                    "stream": "spot",
                    "instrument": "BTCUSDT",
                    "event_type": "ticker",
                    "source_timestamp": requested - timedelta(seconds=0.4),
                    "received_at": requested - timedelta(seconds=0.2),
                    "sequence": "v4-lateral-hour-left",
                    "market_id": None,
                    "asset_id": None,
                    "payload": {"data": {"lastPrice": "50005"}},
                    "dedupe_key": "v4-lateral-hour-left",
                },
                {
                    "id": 910007,
                    "source": "bybit",
                    "stream": "spot",
                    "instrument": "BTCUSDT",
                    "event_type": "ticker",
                    "source_timestamp": requested,
                    "received_at": requested,
                    "sequence": "v4-lateral-hour-right",
                    "market_id": None,
                    "asset_id": None,
                    "payload": {"data": {"lastPrice": "50006"}},
                    "dedupe_key": "v4-lateral-hour-right",
                },
            ],
        )

    statements: list[str] = []

    def capture(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        statements.append(statement)

    reader = V4SourceTimeReader()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        with engine.connect() as connection:
            reader.prefetch_candidate_rows(
                connection,
                (
                    (
                        "bybit",
                        "spot",
                        "BTCUSDT",
                        requested,
                        False,
                    ),
                ),
            )
            statement_count_after_prefetch = len(statements)
            observation, _ = reader.latest_price(
                connection,
                source="bybit",
                stream="spot",
                instrument="BTCUSDT",
                requested_at=requested,
            )
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    key = reader._candidate_cache_key(
        source="bybit",
        stream="spot",
        instrument="BTCUSDT",
        requested_at=requested,
        ticker_only=False,
    )
    assert [
        int(row["id"])
        for row in reader._prefetched_candidate_rows[key]
    ] == [910007, 910006]
    assert observation is not None
    assert float(observation.price) == 50006.0
    assert len(statements) == statement_count_after_prefetch

    candidate_statements = [
        statement
        for statement in statements
        if "CROSS JOIN LATERAL" in statement
    ]
    assert len(candidate_statements) == 2
    assert any(
        "raw_market_events_20260904_11" in statement
        for statement in candidate_statements
    )
    assert any(
        "raw_market_events_20260904_12" in statement
        for statement in candidate_statements
    )


def test_existing_hour_partition_does_not_repair_missing_v4_lookup_index(
    engine,
) -> None:
    now = datetime(2026, 9, 4, 11, 15, tzinfo=UTC)
    ensure_partitioned_raw_storage(
        engine,
        now=now,
        migrate_existing=False,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                "DROP INDEX "
                "raw_market_events_20260904_11_v4_source_lookup_idx"
            )
        )
        ensure_hour_partitions(
            connection,
            start_at=datetime(2026, 9, 4, 11, 0, tzinfo=UTC),
            hours_ahead=0,
        )
        index_exists = connection.execute(
            text(
                """
                SELECT to_regclass(
                    'raw_market_events_20260904_11_v4_source_lookup_idx'
                )
                """
            )
        ).scalar_one()

    assert index_exists is None

def test_hour_partition_creation_fails_fast_behind_reader_lock(engine) -> None:
    now = datetime(2026, 9, 4, 11, 15, tzinfo=UTC)
    ensure_partitioned_raw_storage(
        engine,
        now=now,
        migrate_existing=False,
    )

    blocker = engine.connect()
    transaction = blocker.begin()
    try:
        blocker.execute(text("LOCK TABLE raw_market_events IN ACCESS SHARE MODE"))

        started = monotonic()
        with pytest.raises(DBAPIError):
            with engine.begin() as connection:
                ensure_hour_partitions(
                    connection,
                    start_at=datetime(2026, 9, 4, 14, 0, tzinfo=UTC),
                    hours_ahead=0,
                )
        elapsed = monotonic() - started
    finally:
        transaction.rollback()
        blocker.close()

    assert elapsed < 5.0


def test_existing_hour_partition_does_not_wait_for_parent_ddl_lock(engine) -> None:
    now = datetime(2026, 9, 4, 11, 15, tzinfo=UTC)
    ensure_partitioned_raw_storage(
        engine,
        now=now,
        migrate_existing=False,
    )

    blocker = engine.connect()
    transaction = blocker.begin()
    try:
        blocker.execute(text("LOCK TABLE raw_market_events IN ACCESS SHARE MODE"))
        started = monotonic()
        with engine.begin() as connection:
            partitions = ensure_hour_partitions(
                connection,
                start_at=datetime(2026, 9, 4, 11, 0, tzinfo=UTC),
                hours_ahead=0,
            )
        elapsed = monotonic() - started
    finally:
        transaction.rollback()
        blocker.close()

    assert [item.name for item in partitions] == ["raw_market_events_20260904_11"]
    assert elapsed < 1.0


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



def _event(received_at: datetime, sequence: int = 1) -> RawEvent:
    return RawEvent.build(
        source="polymarket",
        stream="market",
        instrument="partitioned-dedupe-condition",
        event_type="last_trade_price",
        source_timestamp=received_at,
        received_at=received_at,
        sequence=sequence,
        market_id="partitioned-dedupe-condition",
        asset_id="partitioned-dedupe-token",
        payload={"sequence": sequence, "price": "0.51"},
    )


def test_partitioned_writer_preserves_replay_dedupe(engine) -> None:
    now = datetime(2026, 9, 4, 12, 5, tzinfo=UTC)
    ensure_partitioned_raw_storage(engine, now=now)
    repository = RecorderRepository()
    event = _event(now)

    with engine.begin() as connection:
        assert repository.insert_events(connection, [event]) == 1
        assert repository.insert_events(connection, [event]) == 0

    with engine.connect() as connection:
        raw_count = connection.execute(
            text(
                """
                SELECT count(*)
                FROM raw_market_events
                WHERE dedupe_key = :dedupe_key
                """
            ),
            {"dedupe_key": event.dedupe_key},
        ).scalar_one()
        ledger_count = connection.execute(
            text(
                """
                SELECT count(*)
                FROM raw_event_dedupe
                WHERE dedupe_key = :dedupe_key
                """
            ),
            {"dedupe_key": event.dedupe_key},
        ).scalar_one()

    assert raw_count == 1
    assert ledger_count == 1


def test_partitioned_writer_concurrent_duplicate_race_persists_one_row(engine) -> None:
    now = datetime(2026, 9, 4, 13, 5, tzinfo=UTC)
    ensure_partitioned_raw_storage(engine, now=now)
    event = _event(now, sequence=2)
    barrier = Barrier(2)

    def write_once() -> int:
        repository = RecorderRepository()
        with engine.begin() as connection:
            barrier.wait(timeout=5)
            return repository.insert_events(connection, [event])

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: write_once(), range(2)))

    assert sorted(results) == [0, 1]
    with engine.connect() as connection:
        raw_count = connection.execute(
            text(
                """
                SELECT count(*)
                FROM raw_market_events
                WHERE dedupe_key = :dedupe_key
                """
            ),
            {"dedupe_key": event.dedupe_key},
        ).scalar_one()
        ledger_count = connection.execute(
            text(
                """
                SELECT count(*)
                FROM raw_event_dedupe
                WHERE dedupe_key = :dedupe_key
                """
            ),
            {"dedupe_key": event.dedupe_key},
        ).scalar_one()

    assert raw_count == 1
    assert ledger_count == 1


def _install_compact_dedupe_digest_indexes(engine) -> None:
    with engine.begin() as connection:
        for remainder in range(16):
            table_name = f"raw_event_dedupe_h{remainder:02d}"
            index_name = f"{table_name}_digest_uidx"
            connection.execute(
                text(
                    f"""
                    CREATE UNIQUE INDEX {index_name}
                    ON {table_name} (
                        (decode(substring(dedupe_key FROM 8), 'hex'))
                    )
                    """
                )
            )
        connection.execute(
            text(
                """
                ALTER TABLE raw_event_dedupe
                DROP CONSTRAINT raw_event_dedupe_pkey
                """
            )
        )


def test_partitioned_writer_compact_digest_indexes_preserve_replay_dedupe(engine) -> None:
    now = datetime(2026, 9, 4, 13, 10, tzinfo=UTC)
    ensure_partitioned_raw_storage(engine, now=now)
    _install_compact_dedupe_digest_indexes(engine)
    repository = RecorderRepository()
    event = _event(now, sequence=20)

    with engine.begin() as connection:
        assert repository.insert_events(connection, [event]) == 1
        assert repository.insert_events(connection, [event]) == 0

    with engine.connect() as connection:
        ledger_count = connection.execute(
            text(
                """
                SELECT count(*)
                FROM raw_event_dedupe
                WHERE dedupe_key = :dedupe_key
                """
            ),
            {"dedupe_key": event.dedupe_key},
        ).scalar_one()
        primary_count = connection.execute(
            text(
                """
                SELECT count(*)
                FROM pg_index AS index_meta
                JOIN pg_class AS table_relation
                  ON table_relation.oid = index_meta.indrelid
                WHERE table_relation.relname LIKE 'raw_event_dedupe_h__'
                  AND index_meta.indisprimary
                """
            )
        ).scalar_one()
        digest_unique_count = connection.execute(
            text(
                """
                SELECT count(*)
                FROM pg_index AS index_meta
                JOIN pg_class AS table_relation
                  ON table_relation.oid = index_meta.indrelid
                JOIN pg_class AS index_relation
                  ON index_relation.oid = index_meta.indexrelid
                WHERE table_relation.relname LIKE 'raw_event_dedupe_h__'
                  AND index_relation.relname LIKE 'raw_event_dedupe_h%_digest_uidx'
                  AND index_meta.indisunique
                  AND index_meta.indisvalid
                  AND index_meta.indisready
                """
            )
        ).scalar_one()

    assert ledger_count == 1
    assert primary_count == 0
    assert digest_unique_count == 16


@pytest.mark.asyncio
async def test_compact_dedupe_two_batched_writers_persist_distinct_events(engine) -> None:
    now = datetime(2026, 9, 4, 13, 20, tzinfo=UTC)
    ensure_partitioned_raw_storage(engine, now=now)
    _install_compact_dedupe_digest_indexes(engine)
    repository = RecorderRepository()
    buffer = EventBuffer(maxsize=200)
    raw_insert_executemany: list[bool] = []

    def capture_raw_insert(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        executemany: bool,
    ) -> None:
        if statement.lstrip().startswith("INSERT INTO raw_market_events"):
            raw_insert_executemany.append(executemany)

    event.listen(engine, "before_cursor_execute", capture_raw_insert)

    async def sink(items: list[RawEvent]) -> None:
        def write() -> None:
            with engine.begin() as connection:
                repository.insert_events(connection, items)

        await asyncio.to_thread(write)

    writer = BatchWriter(
        buffer=buffer,
        sink=sink,
        batch_size=20,
        flush_interval_seconds=0.01,
        worker_count=2,
    )
    for sequence in range(120):
        await buffer.put(
            _event(
                now + timedelta(milliseconds=sequence),
                sequence=1000 + sequence,
            )
        )

    stop = asyncio.Event()
    stop.set()
    try:
        await asyncio.wait_for(writer.run(stop), timeout=10)
    finally:
        event.remove(engine, "before_cursor_execute", capture_raw_insert)

    with engine.connect() as connection:
        raw_count = connection.execute(
            text("SELECT count(*) FROM raw_market_events")
        ).scalar_one()
        ledger_count = connection.execute(
            text("SELECT count(*) FROM raw_event_dedupe")
        ).scalar_one()

    assert raw_count == 120
    assert ledger_count == 120
    assert raw_insert_executemany
    assert all(raw_insert_executemany)


def test_partitioned_writer_compact_digest_indexes_preserve_duplicate_race(engine) -> None:
    now = datetime(2026, 9, 4, 13, 15, tzinfo=UTC)
    ensure_partitioned_raw_storage(engine, now=now)
    _install_compact_dedupe_digest_indexes(engine)
    event = _event(now, sequence=21)
    barrier = Barrier(2)

    def write_once() -> int:
        repository = RecorderRepository()
        with engine.begin() as connection:
            barrier.wait(timeout=5)
            return repository.insert_events(connection, [event])

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: write_once(), range(2)))

    assert sorted(results) == [0, 1]
    with engine.connect() as connection:
        raw_count = connection.execute(
            text(
                """
                SELECT count(*)
                FROM raw_market_events
                WHERE dedupe_key = :dedupe_key
                """
            ),
            {"dedupe_key": event.dedupe_key},
        ).scalar_one()
        ledger_count = connection.execute(
            text(
                """
                SELECT count(*)
                FROM raw_event_dedupe
                WHERE dedupe_key = :dedupe_key
                """
            ),
            {"dedupe_key": event.dedupe_key},
        ).scalar_one()

    assert raw_count == 1
    assert ledger_count == 1


def test_partitioned_runtime_ensure_does_not_wait_on_active_dedupe_writer(engine) -> None:
    now = datetime(2026, 9, 4, 14, 5, tzinfo=UTC)
    ensure_partitioned_raw_storage(engine, now=now)

    blocker = engine.connect()
    transaction = blocker.begin()
    blocker.execute(
        text(
            """
            INSERT INTO raw_event_dedupe (dedupe_key, received_at)
            VALUES (:dedupe_key, :received_at)
            """
        ),
        {
            "dedupe_key": "runtime-ensure-active-dedupe-writer",
            "received_at": now,
        },
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            ensure_partitioned_raw_storage,
            engine,
            now=now + timedelta(hours=1),
        )
        try:
            result = future.result(timeout=2.0)
        except TimeoutError:
            raise AssertionError(
                "partitioned runtime ensure waited on an active dedupe writer"
            ) from None
        finally:
            transaction.rollback()
            blocker.close()

    assert result.mode is RawStorageMode.PARTITIONED
    assert result.migrated_rows == 0
    assert result.rollback_table is None


def test_partitioned_runtime_ensure_fails_closed_on_missing_dedupe_index(engine) -> None:
    now = datetime(2026, 9, 4, 14, 5, tzinfo=UTC)
    ensure_partitioned_raw_storage(engine, now=now)

    with engine.begin() as connection:
        connection.execute(text("DROP INDEX ix_raw_event_dedupe_received_at"))

    with pytest.raises(
        RuntimeError,
        match="partitioned dedupe parent indexes are missing",
    ):
        ensure_partitioned_raw_storage(
            engine,
            now=now + timedelta(hours=1),
        )


def test_partitioned_runtime_ensure_accepts_compact_dedupe_contract(engine) -> None:
    now = datetime(2026, 9, 4, 14, 10, tzinfo=UTC)
    ensure_partitioned_raw_storage(engine, now=now)
    _install_compact_dedupe_digest_indexes(engine)

    result = ensure_partitioned_raw_storage(
        engine,
        now=now + timedelta(hours=1),
    )

    assert result.mode is RawStorageMode.PARTITIONED
    assert result.migrated_rows == 0
    assert result.rollback_table is None


def test_partitioned_runtime_ensure_rejects_partial_compact_dedupe_contract(engine) -> None:
    now = datetime(2026, 9, 4, 14, 15, tzinfo=UTC)
    ensure_partitioned_raw_storage(engine, now=now)
    _install_compact_dedupe_digest_indexes(engine)

    with engine.begin() as connection:
        connection.execute(text("DROP INDEX raw_event_dedupe_h00_digest_uidx"))

    with pytest.raises(
        RuntimeError,
        match="partitioned dedupe uniqueness contract is missing or unhealthy",
    ):
        ensure_partitioned_raw_storage(
            engine,
            now=now + timedelta(hours=1),
        )


def test_partitioned_writer_missing_hour_rolls_back_dedupe_claim(engine) -> None:
    now = datetime(2026, 9, 4, 14, 5, tzinfo=UTC)
    ensure_partitioned_raw_storage(engine, now=now)
    event = _event(now, sequence=3)
    start_at = now.replace(minute=0, second=0, microsecond=0)
    end_at = start_at + timedelta(hours=1)

    with engine.begin() as connection:
        dropped = drop_raw_partition(
            connection,
            start_at=start_at,
            end_at=end_at,
        )
    assert dropped == "raw_market_events_20260904_14"

    repository = RecorderRepository()
    with pytest.raises(DBAPIError, match="no partition"):
        with engine.begin() as connection:
            repository.insert_events(connection, [event])

    with engine.connect() as connection:
        ledger_count = connection.execute(
            text(
                """
                SELECT count(*)
                FROM raw_event_dedupe
                WHERE dedupe_key = :dedupe_key
                """
            ),
            {"dedupe_key": event.dedupe_key},
        ).scalar_one()

    assert ledger_count == 0



def _archive_paths(archive_dir, manifest):
    return (
        archive_dir / manifest.archive_name,
        archive_dir / f"{manifest.archive_name}.manifest.json",
    )


def _advance_required_compact_feeds(engine, at: datetime) -> None:
    market_state_1s.create(engine, checkfirst=True)
    repository = RecorderRepository()
    feeds = (
        ("bybit", "spot", "BTCUSDT"),
        ("bybit", "linear", "BTCUSDT"),
        ("coinbase", "spot", "BTC-USD"),
        ("polymarket", "market", "partitioned-dedupe-condition"),
    )
    snapshots = [
        MarketStateSnapshot(
            bucket_at=at,
            state_key=f"partitioned-raw-test/{source}/{stream}",
            source=source,
            stream=stream,
            instrument=instrument,
            last_event_at=at,
            state={"test": True},
        )
        for source, stream, instrument in feeds
    ]
    with engine.begin() as connection:
        repository.upsert_state_snapshots(connection, snapshots)


def _seed_partitioned_hour(engine, start_at: datetime, count: int = 2) -> list[RawEvent]:
    ensure_partitioned_raw_storage(
        engine,
        now=start_at + timedelta(minutes=1),
    )
    events = [
        _event(start_at + timedelta(minutes=5 + index), sequence=100 + index)
        for index in range(count)
    ]
    repository = RecorderRepository()
    with engine.begin() as connection:
        assert repository.insert_events(connection, events) == count
    return events


def test_partition_retirement_missing_archive_keeps_raw_and_dedupe(engine, tmp_path) -> None:
    start_at = datetime(2026, 9, 4, 15, tzinfo=UTC)
    events = _seed_partitioned_hour(engine, start_at, count=1)
    end_at = start_at + timedelta(hours=1)

    with pytest.raises(ArchiveVerificationError, match="missing"):
        retire_verified_partition(
            engine,
            tmp_path / "missing.jsonl.gz",
            tmp_path / "missing.jsonl.gz.manifest.json",
        )

    assert any(item.start_at == start_at for item in list_raw_partitions(engine))
    with engine.connect() as connection:
        raw_count = connection.execute(
            text(
                """
                SELECT count(*) FROM raw_market_events
                WHERE received_at >= :start_at AND received_at < :end_at
                """
            ),
            {"start_at": start_at, "end_at": end_at},
        ).scalar_one()
        ledger_count = connection.execute(
            text(
                """
                SELECT count(*) FROM raw_event_dedupe
                WHERE dedupe_key = :dedupe_key
                """
            ),
            {"dedupe_key": events[0].dedupe_key},
        ).scalar_one()
    assert raw_count == 1
    assert ledger_count == 1


def test_partition_retirement_corrupt_archive_keeps_partition(engine, tmp_path) -> None:
    start_at = datetime(2026, 9, 4, 16, tzinfo=UTC)
    _seed_partitioned_hour(engine, start_at)
    end_at = start_at + timedelta(hours=1)
    archive_dir = tmp_path / "archive"
    manifest = archive_interval(engine, archive_dir, start_at, end_at)
    archive_path, manifest_path = _archive_paths(archive_dir, manifest)
    archive_path.write_bytes(archive_path.read_bytes() + b"tamper")

    with pytest.raises(ArchiveVerificationError, match="SHA-256"):
        retire_verified_partition(engine, archive_path, manifest_path)

    assert any(item.start_at == start_at for item in list_raw_partitions(engine))


def test_partition_retirement_requires_compact_state_advance(engine, tmp_path) -> None:
    start_at = datetime(2026, 9, 4, 17, tzinfo=UTC)
    _seed_partitioned_hour(engine, start_at)
    end_at = start_at + timedelta(hours=1)
    archive_dir = tmp_path / "archive"
    manifest = archive_interval(engine, archive_dir, start_at, end_at)
    archive_path, manifest_path = _archive_paths(archive_dir, manifest)

    with pytest.raises(RuntimeError, match="compact state"):
        retire_verified_partition(engine, archive_path, manifest_path)

    assert any(item.start_at == start_at for item in list_raw_partitions(engine))


def test_terminal_partial_partition_retirement_requires_explicit_recovery_opt_in(
    engine,
    tmp_path,
) -> None:
    start_at = datetime(2026, 9, 4, 18, tzinfo=UTC)
    events = _seed_partitioned_hour(engine, start_at)
    end_at = start_at + timedelta(hours=1)
    last_raw_at = max(event.received_at for event in events)
    archive_dir = tmp_path / "archive"
    manifest = archive_interval(engine, archive_dir, start_at, end_at)
    archive_path, manifest_path = _archive_paths(archive_dir, manifest)
    _advance_required_compact_feeds(engine, last_raw_at + timedelta(seconds=1))

    with pytest.raises(RuntimeError, match="compact state"):
        retire_verified_partition(engine, archive_path, manifest_path)

    result = retire_verified_partition(
        engine,
        archive_path,
        manifest_path,
        batch_size=1,
        allow_terminal_partial_compact_cutoff=True,
    )

    assert result.archived_rows == len(events)
    assert result.dedupe_rows_removed == len(events)
    assert result.compact_cutoff_at == last_raw_at
    assert result.terminal_partial_compact_cutoff is True


def test_terminal_partial_recovery_cutoff_rejects_partition_when_later_raw_exists(
    engine,
    tmp_path,
) -> None:
    start_at = datetime(2026, 9, 4, 19, tzinfo=UTC)
    events = _seed_partitioned_hour(engine, start_at)
    _seed_partitioned_hour(engine, start_at + timedelta(hours=1), count=1)
    end_at = start_at + timedelta(hours=1)
    last_raw_at = max(event.received_at for event in events)
    archive_dir = tmp_path / "archive"
    manifest = archive_interval(engine, archive_dir, start_at, end_at)
    archive_path, manifest_path = _archive_paths(archive_dir, manifest)
    _advance_required_compact_feeds(engine, last_raw_at + timedelta(seconds=1))

    with pytest.raises(RuntimeError, match="compact state"):
        retire_verified_partition(
            engine,
            archive_path,
            manifest_path,
            allow_terminal_partial_compact_cutoff=True,
        )

    assert any(item.start_at == start_at for item in list_raw_partitions(engine))


def test_verified_partition_retirement_drops_relation_then_dedupe(engine, tmp_path) -> None:
    start_at = datetime(2026, 9, 4, 18, tzinfo=UTC)
    events = _seed_partitioned_hour(engine, start_at)
    end_at = start_at + timedelta(hours=1)
    archive_dir = tmp_path / "archive"
    manifest = archive_interval(engine, archive_dir, start_at, end_at)
    archive_path, manifest_path = _archive_paths(archive_dir, manifest)
    _advance_required_compact_feeds(engine, end_at + timedelta(seconds=1))
    partition_name = "raw_market_events_20260904_18"

    with engine.connect() as connection:
        relation_bytes = connection.execute(
            text("SELECT pg_total_relation_size(to_regclass(:name))"),
            {"name": partition_name},
        ).scalar_one()
    assert relation_bytes > 0

    result = retire_verified_partition(
        engine,
        archive_path,
        manifest_path,
        batch_size=1,
    )

    assert result.partition_name == partition_name
    assert result.archived_rows == len(events)
    assert result.dedupe_rows_removed == len(events)
    assert result.compact_cutoff_at == end_at
    assert result.terminal_partial_compact_cutoff is False
    with engine.connect() as connection:
        relation = connection.execute(
            text("SELECT to_regclass(:name)"),
            {"name": partition_name},
        ).scalar_one()
        ledger_count = connection.execute(
            text(
                """
                SELECT count(*) FROM raw_event_dedupe
                WHERE received_at >= :start_at AND received_at < :end_at
                """
            ),
            {"start_at": start_at, "end_at": end_at},
        ).scalar_one()

    assert relation is None
    assert ledger_count == 0


def test_partition_retirement_concurrent_detach_allows_active_writer_progress(
    engine,
    tmp_path,
) -> None:
    start_at = datetime(2026, 9, 4, 21, tzinfo=UTC)
    events = _seed_partitioned_hour(engine, start_at, count=2)
    end_at = start_at + timedelta(hours=1)
    writer_at = start_at + timedelta(hours=2, minutes=5)
    archive_dir = tmp_path / "archive"
    manifest = archive_interval(engine, archive_dir, start_at, end_at)
    archive_path, manifest_path = _archive_paths(archive_dir, manifest)
    _advance_required_compact_feeds(engine, end_at + timedelta(seconds=1))
    partition_name = "raw_market_events_20260904_21"
    repository = RecorderRepository()

    blocker = engine.connect()
    blocker_transaction = blocker.begin()
    assert repository.insert_events(
        blocker,
        [_event(writer_at, sequence=501)],
    ) == 1

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        retirement = executor.submit(
            retire_verified_partition,
            engine,
            archive_path,
            manifest_path,
        )

        deadline = monotonic() + 3.0
        detach_pending = False
        while monotonic() < deadline:
            with engine.connect() as connection:
                detach_pending = bool(
                    connection.execute(
                        text(
                            """
                            SELECT COALESCE(bool_or(inheritance.inhdetachpending), false)
                            FROM pg_inherits AS inheritance
                            JOIN pg_class AS child ON child.oid = inheritance.inhrelid
                            JOIN pg_class AS parent ON parent.oid = inheritance.inhparent
                            WHERE parent.relname = 'raw_market_events'
                              AND child.relname = :partition_name
                            """
                        ),
                        {"partition_name": partition_name},
                    ).scalar_one()
                )
            if detach_pending:
                break
            sleep(0.02)

        assert detach_pending, "retirement never entered concurrent detach"

        def write_while_detach_waits() -> int:
            with engine.begin() as connection:
                return repository.insert_events(
                    connection,
                    [_event(writer_at + timedelta(seconds=1), sequence=502)],
                )

        writer = executor.submit(write_while_detach_waits)
        assert writer.result(timeout=2.0) == 1

        blocker_transaction.commit()
        result = retirement.result(timeout=5.0)
    finally:
        if blocker_transaction.is_active:
            blocker_transaction.rollback()
        blocker.close()
        executor.shutdown(wait=True, cancel_futures=True)

    assert result.partition_name == partition_name
    assert result.archived_rows == len(events)
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT to_regclass(:name)"),
            {"name": partition_name},
        ).scalar_one() is None
        current_rows = connection.execute(
            text(
                """
                SELECT count(*)
                FROM raw_market_events
                WHERE received_at >= :start_at
                """
            ),
            {"start_at": writer_at},
        ).scalar_one()
    assert current_rows == 2


def test_partition_retirement_finalizes_interrupted_concurrent_detach(
    engine,
    tmp_path,
) -> None:
    start_at = datetime(2026, 9, 4, 23, tzinfo=UTC)
    events = _seed_partitioned_hour(engine, start_at, count=2)
    end_at = start_at + timedelta(hours=1)
    writer_at = start_at + timedelta(hours=2, minutes=5)
    archive_dir = tmp_path / "archive"
    manifest = archive_interval(engine, archive_dir, start_at, end_at)
    archive_path, manifest_path = _archive_paths(archive_dir, manifest)
    _advance_required_compact_feeds(engine, end_at + timedelta(seconds=1))
    partition_name = "raw_market_events_20260904_23"
    repository = RecorderRepository()

    blocker = engine.connect()
    blocker_transaction = blocker.begin()
    assert repository.insert_events(
        blocker,
        [_event(writer_at, sequence=601)],
    ) == 1

    worker_ready = Barrier(2)
    backend_pid: dict[str, int] = {}

    def interrupted_detach() -> None:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            backend_pid["value"] = int(
                connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
            )
            worker_ready.wait(timeout=5)
            connection.execute(
                text(
                    "ALTER TABLE raw_market_events "
                    f"DETACH PARTITION {partition_name} CONCURRENTLY"
                )
            )

    executor = ThreadPoolExecutor(max_workers=1)
    detach = executor.submit(interrupted_detach)
    try:
        worker_ready.wait(timeout=5)
        deadline = monotonic() + 3.0
        detach_pending = False
        while monotonic() < deadline:
            with engine.connect() as connection:
                detach_pending = bool(
                    connection.execute(
                        text(
                            """
                            SELECT COALESCE(bool_or(inheritance.inhdetachpending), false)
                            FROM pg_inherits AS inheritance
                            JOIN pg_class AS child ON child.oid = inheritance.inhrelid
                            JOIN pg_class AS parent ON parent.oid = inheritance.inhparent
                            WHERE parent.relname = 'raw_market_events'
                              AND child.relname = :partition_name
                            """
                        ),
                        {"partition_name": partition_name},
                    ).scalar_one()
                )
            if detach_pending:
                break
            sleep(0.02)
        assert detach_pending, "concurrent detach never reached pending state"

        with engine.begin() as connection:
            cancelled = connection.execute(
                text("SELECT pg_cancel_backend(:pid)"),
                {"pid": backend_pid["value"]},
            ).scalar_one()
        assert cancelled is True

        with pytest.raises(DBAPIError):
            detach.result(timeout=2.0)
    finally:
        if blocker_transaction.is_active:
            blocker_transaction.commit()
        blocker.close()
        executor.shutdown(wait=True, cancel_futures=True)

    with engine.connect() as connection:
        pending_after_cancel = bool(
            connection.execute(
                text(
                    """
                    SELECT COALESCE(bool_or(inheritance.inhdetachpending), false)
                    FROM pg_inherits AS inheritance
                    JOIN pg_class AS child ON child.oid = inheritance.inhrelid
                    JOIN pg_class AS parent ON parent.oid = inheritance.inhparent
                    WHERE parent.relname = 'raw_market_events'
                      AND child.relname = :partition_name
                    """
                ),
                {"partition_name": partition_name},
            ).scalar_one()
        )
    assert pending_after_cancel is True

    result = retire_verified_partition(
        engine,
        archive_path,
        manifest_path,
        batch_size=1,
    )

    assert result.partition_name == partition_name
    assert result.archived_rows == len(events)
    assert result.dedupe_rows_removed == len(events)
    with engine.connect() as connection:
        relation = connection.execute(
            text("SELECT to_regclass(:name)"),
            {"name": partition_name},
        ).scalar_one()
    assert relation is None


def test_partition_retirement_resumes_detached_table_before_physical_drop(
    engine,
    tmp_path,
) -> None:
    start_at = datetime(2026, 9, 4, 22, tzinfo=UTC)
    events = _seed_partitioned_hour(engine, start_at, count=2)
    end_at = start_at + timedelta(hours=1)
    archive_dir = tmp_path / "archive"
    manifest = archive_interval(engine, archive_dir, start_at, end_at)
    archive_path, manifest_path = _archive_paths(archive_dir, manifest)
    _advance_required_compact_feeds(engine, end_at + timedelta(seconds=1))
    partition_name = "raw_market_events_20260904_22"

    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE raw_market_events "
                f"DETACH PARTITION {partition_name}"
            )
        )

    assert all(
        item.name != partition_name
        for item in list_raw_partitions(engine)
    )
    assert any(
        item.name == partition_name
        for item in list_raw_retirement_candidates(engine)
    )

    health_now = end_at + timedelta(hours=2, minutes=30)
    ensure_partitioned_raw_storage(engine, now=health_now)
    _record_maintenance_success(engine, health_now - timedelta(minutes=5))
    health = build_composite_storage_health(
        engine,
        tmp_path,
        _storage_settings(tmp_path, hot_raw_hours=1),
        now=health_now,
    )
    assert health["guards"]["current_partition_present"] is True
    assert health["guards"]["maintenance_fresh"] is True
    assert health["guards"]["retention_current"] is False
    assert health["raw_partitions"]["oldest_start_at"] == start_at.isoformat().replace(
        "+00:00",
        "Z",
    )

    removed = prune_expired_archives(
        engine,
        archive_dir,
        now=health_now,
        retention_hours=1,
    )
    assert removed == []
    assert archive_path.exists()
    assert manifest_path.exists()

    result = retire_verified_partition(
        engine,
        archive_path,
        manifest_path,
        batch_size=1,
    )

    assert result.partition_name == partition_name
    assert result.archived_rows == len(events)
    assert result.dedupe_rows_removed == len(events)
    with engine.connect() as connection:
        relation = connection.execute(
            text("SELECT to_regclass(:name)"),
            {"name": partition_name},
        ).scalar_one()
    assert relation is None


def test_partition_retirement_uses_partition_local_ctid_dedupe_batches(engine, tmp_path) -> None:
    start_at = datetime(2026, 9, 4, 20, tzinfo=UTC)
    events = _seed_partitioned_hour(engine, start_at, count=32)
    end_at = start_at + timedelta(hours=1)
    archive_dir = tmp_path / "archive"
    manifest = archive_interval(engine, archive_dir, start_at, end_at)
    archive_path, manifest_path = _archive_paths(archive_dir, manifest)
    _advance_required_compact_feeds(engine, end_at + timedelta(seconds=1))
    statements: list[str] = []

    def capture_statement(conn, cursor, statement, parameters, context, executemany):
        if "DELETE FROM raw_event_dedupe" in statement:
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture_statement)
    try:
        result = retire_verified_partition(
            engine, archive_path, manifest_path, batch_size=16
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture_statement)

    assert result.dedupe_rows_removed == len(events)
    assert statements
    assert all("DELETE FROM raw_event_dedupe_h" in statement for statement in statements)
    assert all("ctid" in statement for statement in statements)
    assert all(
        "DELETE FROM raw_event_dedupe AS target" not in statement
        for statement in statements
    )


def _storage_settings(tmp_path, *, hot_raw_hours: int = 24) -> Settings:
    return Settings(
        _env_file=None,
        storage_archive_dir=str(tmp_path / "archive"),
        storage_health_path=str(tmp_path),
        storage_hot_raw_hours=hot_raw_hours,
        storage_warning_free_gib=0,
        storage_critical_free_gib=0,
        storage_maintenance_max_age_hours=2,
    )


def _record_maintenance_success(engine, completed_at: datetime) -> None:
    storage_maintenance_runs.create(engine, checkfirst=True)
    with engine.begin() as connection:
        connection.execute(
            insert(storage_maintenance_runs).values(
                started_at=completed_at - timedelta(minutes=1),
                completed_at=completed_at,
                status="success",
                storage_mode="partitioned",
                partitions_retired=0,
                dedupe_rows_removed=0,
                disk_status="ok",
                error=None,
            )
        )


def test_partitioned_health_requires_fresh_maintenance_heartbeat(engine, tmp_path) -> None:
    now = datetime(2026, 9, 5, 12, 30, tzinfo=UTC)
    ensure_partitioned_raw_storage(engine, now=now)
    settings = _storage_settings(tmp_path)

    missing = build_composite_storage_health(
        engine,
        tmp_path,
        settings,
        now=now,
    )
    assert missing["status"] == "critical"
    assert missing["guards"]["maintenance_fresh"] is False
    assert missing["maintenance"]["last_success_at"] is None

    _record_maintenance_success(engine, now - timedelta(hours=3))
    stale = build_composite_storage_health(
        engine,
        tmp_path,
        settings,
        now=now,
    )
    assert stale["status"] == "critical"
    assert stale["guards"]["maintenance_fresh"] is False
    assert stale["maintenance"]["age_hours"] == pytest.approx(3.0)

    _record_maintenance_success(engine, now - timedelta(minutes=30))
    healthy = build_composite_storage_health(
        engine,
        tmp_path,
        settings,
        now=now,
    )
    assert healthy["status"] == "ok"
    assert healthy["guards"] == {
        "maintenance_fresh": True,
        "current_partition_present": True,
        "retention_current": True,
    }


def test_partitioned_health_fails_when_current_hour_partition_is_missing(engine, tmp_path) -> None:
    now = datetime(2026, 9, 5, 13, 30, tzinfo=UTC)
    ensure_partitioned_raw_storage(engine, now=now)
    _record_maintenance_success(engine, now - timedelta(minutes=15))
    current = now.replace(minute=0, second=0, microsecond=0)
    with engine.begin() as connection:
        assert drop_raw_partition(
            connection,
            start_at=current,
            end_at=current + timedelta(hours=1),
        ) is not None

    report = build_composite_storage_health(
        engine,
        tmp_path,
        _storage_settings(tmp_path),
        now=now,
    )

    assert report["status"] == "critical"
    assert report["guards"]["current_partition_present"] is False



def test_partitioned_health_tolerates_one_additional_hourly_cycle(engine, tmp_path) -> None:
    now = datetime(2026, 9, 5, 15, 30, tzinfo=UTC)
    ensure_partitioned_raw_storage(engine, now=now)
    _record_maintenance_success(engine, now - timedelta(minutes=10))
    tolerated_start = (
        now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=25)
    )
    with engine.begin() as connection:
        ensure_hour_partitions(
            connection,
            start_at=tolerated_start,
            hours_ahead=0,
        )

    report = build_composite_storage_health(
        engine,
        tmp_path,
        _storage_settings(tmp_path, hot_raw_hours=24),
        now=now,
    )

    assert report["status"] == "ok"
    assert report["guards"]["retention_current"] is True
    assert report["raw_partitions"]["retention_lag_hours"] == pytest.approx(1.0)


def test_partitioned_health_fails_on_expired_partition_retention_lag(engine, tmp_path) -> None:
    now = datetime(2026, 9, 5, 14, 30, tzinfo=UTC)
    ensure_partitioned_raw_storage(engine, now=now)
    _record_maintenance_success(engine, now - timedelta(minutes=10))
    old_start = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=26)
    with engine.begin() as connection:
        ensure_hour_partitions(
            connection,
            start_at=old_start,
            hours_ahead=0,
        )

    report = build_composite_storage_health(
        engine,
        tmp_path,
        _storage_settings(tmp_path, hot_raw_hours=24),
        now=now,
    )

    assert report["status"] == "critical"
    assert report["guards"]["retention_current"] is False
    assert report["raw_partitions"]["retention_lag_hours"] >= 1.0



def test_partitioned_migration_rollback_restores_legacy_table_and_constraints(engine) -> None:
    base = datetime(2026, 9, 5, 16, 5, tzinfo=UTC)
    rows = [
        {
            "id": 501,
            "source": "bybit",
            "stream": "spot",
            "instrument": "BTCUSDT",
            "event_type": "trade",
            "source_timestamp": base,
            "received_at": base,
            "sequence": "501",
            "market_id": None,
            "asset_id": None,
            "payload": {"price": "61000"},
            "dedupe_key": "sha256:" + "d" * 64,
        },
        {
            "id": 502,
            "source": "polymarket",
            "stream": "market",
            "instrument": "rollback-condition",
            "event_type": "book",
            "source_timestamp": base + timedelta(hours=1),
            "received_at": base + timedelta(hours=1),
            "sequence": "502",
            "market_id": "rollback-condition",
            "asset_id": "rollback-token",
            "payload": {"bids": [], "asks": []},
            "dedupe_key": "sha256:" + "e" * 64,
        },
    ]
    with engine.begin() as connection:
        connection.execute(insert(raw_market_events), rows)

    migrated = ensure_partitioned_raw_storage(
        engine,
        now=base + timedelta(hours=1, minutes=10),
        migrate_existing=True,
    )
    assert migrated.mode is RawStorageMode.PARTITIONED
    assert migrated.rollback_table == "raw_market_events_legacy"

    result = rollback_partitioned_raw_storage(engine)

    assert result.restored_table == "raw_market_events"
    assert result.restored_rows == 2
    with engine.connect() as connection:
        assert raw_storage_mode(connection) is RawStorageMode.LEGACY
        assert connection.execute(
            text("SELECT to_regclass('raw_market_events_legacy')")
        ).scalar_one() is None
        assert connection.execute(
            text("SELECT to_regclass('raw_event_dedupe')")
        ).scalar_one() is None
        assert connection.execute(
            text("SELECT to_regclass('raw_market_events_id_seq_v2')")
        ).scalar_one() is None
        actual = connection.execute(
            text(
                """
                SELECT id, dedupe_key, received_at
                FROM raw_market_events
                ORDER BY id
                """
            )
        ).all()
        original_index_names = set(
            connection.execute(
                text(
                    """
                    SELECT indexname
                    FROM pg_indexes
                    WHERE schemaname = current_schema()
                      AND tablename = 'raw_market_events'
                    """
                )
            ).scalars()
        )

    assert actual == [
        (501, "sha256:" + "d" * 64, base),
        (502, "sha256:" + "e" * 64, base + timedelta(hours=1)),
    ]
    assert "uq_raw_market_events_dedupe_key" in original_index_names
    assert not any(name.startswith("legacy_") for name in original_index_names)



def test_streaming_exact_raw_parity_detects_payload_mismatch(engine) -> None:
    base = datetime(2026, 9, 5, 18, 5, tzinfo=UTC)
    rows = [
        {
            "id": 601,
            "source": "coinbase",
            "stream": "spot",
            "instrument": "BTC-USD",
            "event_type": "ticker",
            "source_timestamp": base,
            "received_at": base,
            "sequence": "601",
            "market_id": None,
            "asset_id": None,
            "payload": {"price": "62000"},
            "dedupe_key": "sha256:" + "f" * 64,
        },
        {
            "id": 602,
            "source": "bybit",
            "stream": "linear",
            "instrument": "BTCUSDT",
            "event_type": "trade",
            "source_timestamp": base + timedelta(minutes=1),
            "received_at": base + timedelta(minutes=1),
            "sequence": "602",
            "market_id": None,
            "asset_id": None,
            "payload": {"price": "62001"},
            "dedupe_key": "sha256:" + "0" * 64,
        },
    ]
    with engine.begin() as connection:
        connection.execute(insert(raw_market_events), rows)

    ensure_partitioned_raw_storage(
        engine,
        now=base + timedelta(minutes=10),
        migrate_existing=True,
    )
    assert _exact_raw_parity(engine) == 2

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE raw_market_events
                SET payload = '{"price":"99999"}'::jsonb
                WHERE id = 602
                """
            )
        )

    with pytest.raises(RuntimeError, match="exact row parity failed"):
        _exact_raw_parity(engine)


def test_v4_linear_b0_classifies_null_timestamp_trade_on_partitioned_postgres(
    engine,
) -> None:
    """CI-only PostgreSQL test: include rows that Stage A's null gate excludes."""
    observed = datetime(2026, 10, 12, 21, 8, tzinfo=UTC)
    ensure_partitioned_raw_storage(engine, now=observed, migrate_existing=False)

    def row(id_number, event_type, source_timestamp, payload):
        return {
            "id": id_number,
            "source": "bybit",
            "stream": "linear",
            "instrument": "BTCUSDT",
            "event_type": event_type,
            "source_timestamp": source_timestamp,
            "received_at": observed - timedelta(milliseconds=id_number % 100 + 100),
            "sequence": str(id_number),
            "market_id": None,
            "asset_id": None,
            "payload": payload,
            "dedupe_key": f"b0-synthetic-{id_number}",
        }

    with engine.begin() as writer:
        writer.execute(
            insert(raw_market_events),
            [
                row(
                    921101, "trade", observed - timedelta(milliseconds=300),
                    {"data": [{"p": "65000.1"}]},
                ),
                row(921102, "trade", None, {"data": [{"p": "65000.2"}]}),
                row(
                    921103, "orderbook_delta",
                    observed - timedelta(milliseconds=300),
                    {"data": {"s": "BTCUSDT", "b": [], "a": []}},
                ),
                row(
                    921104, "ticker", observed - timedelta(milliseconds=300),
                    {"data": {"lastPrice": "65000.0"}},
                ),
            ],
        )

    # Use a separate connection with read-only default, as in production.
    readonly = create_engine(
        DATABASE_URL,
        connect_args={"options": "-c default_transaction_read_only=on"},
    )
    try:
        with readonly.connect() as connection:
            result = build_linear_b0_report(connection, observed_at=observed)
    finally:
        readonly.dispose()

    trade = result["classes"]["trade"]
    book = result["classes"]["orderbook"]
    assert trade["rows_sampled"] == 2
    assert trade["nonnull_source_timestamp_rows"] == 1
    assert trade["null_source_timestamp_rows"] == 1
    assert trade["json_array_payload_rows"] == 2
    assert trade["last_trade_price_field_present_rows"] == 2
    assert trade["source_time_eligible_at_observed_rows"] == 1
    assert trade["capped_inconclusive"] is False
    assert book["rows_sampled"] == 1
    assert book["source_time_eligible_at_observed_rows"] == 1
    assert result["max_data_select_queries"] == 2
    assert result["safety"]["database_writes_performed"] is False



def test_stage_b1_offline_partitioned_postgres_actual_insert_classes_and_dedupe(
    engine,
) -> None:
    """Local CI PostgreSQL: count actual committed raw rows, not batch attempts."""
    now = datetime(2026, 9, 4, 11, 15, tzinfo=UTC)
    ensure_partitioned_raw_storage(engine, now=now, migrate_existing=False)
    assert engine.url.host in ("localhost", "127.0.0.1", "::1")

    def build(kind: str, key: str, *, source: str = "bybit") -> RawEvent:
        return RawEvent.build(
            source=source,
            stream="linear",
            instrument="BTCUSDT",
            event_type=kind,
            source_timestamp=now,
            received_at=now,
            sequence=key,
            payload={"i": key, "kind": kind},
        )

    trade = build("trade", "t1")
    book = build("orderbook_delta", "b1")
    liquid = build("liquidation", "l1")
    ticker = build("ticker", "ticker")
    other_feed = build("trade", "coinbase", source="coinbase")
    batch = [trade, trade, book, liquid, ticker, other_feed]

    first = insert_attributed_offline(engine, batch)
    assert first["status"] == "LOCAL_TEST_TRANSACTION_COMMITTED_NO_GO"
    assert first["attempted_by_class"]["trade"] == 2
    assert first["inserted_by_class"] == {
        "trade": 1,
        "orderbook": 1,
        "liquidation": 1,
        "other": 0,
        "excluded_non_bulk": 2,
    }
    assert first["deduplicated_by_class"]["trade"] == 1
    assert first["real_local_db_commit_observed"] is True
    assert first["production_insert_attribution_proven"] is False
    with engine.connect() as connection:
        actual = connection.execute(
            text("SELECT event_type, count(*) FROM raw_market_events "
                 "GROUP BY event_type ORDER BY event_type")
        ).all()
    assert dict(actual) == {
        "liquidation": 1,
        "orderbook_delta": 1,
        "ticker": 1,
        "trade": 2,
    }

    second = insert_attributed_offline(engine, batch)
    assert all(value == 0 for value in second["inserted_by_class"].values())
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM raw_market_events")
        ).scalar_one() == 5


def test_stage_b1_offline_partitioned_transaction_failure_rolls_back(engine) -> None:
    """A failed group must NOT emit a committed-attribution result."""
    now = datetime(2026, 9, 4, 11, 15, tzinfo=UTC)
    ensure_partitioned_raw_storage(engine, now=now, migrate_existing=False)

    def raw(kind: str) -> RawEvent:
        return RawEvent.build(
            source="bybit",
            stream="linear",
            instrument="BTCUSDT",
            event_type=kind,
            source_timestamp=now,
            received_at=now,
            payload={"kind": kind},
        )

    class FailingRepo(RecorderRepository):
        def insert_events(self, connection, events):
            if events and events[0].event_type == "orderbook_delta":
                raise RuntimeError("forced local rollback")
            return super().insert_events(connection, events)

    with pytest.raises(RuntimeError, match="forced local rollback"):
        insert_attributed_offline(
            engine, [raw("trade"), raw("orderbook_delta")],
            repository=FailingRepo(),
        )
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM raw_market_events")
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT count(*) FROM raw_event_dedupe")
        ).scalar_one() == 0


def test_b1_same_batch_insert_claims_match_normal_partitioned_SQL(engine) -> None:
    """Opt-in attribution changes no SQL statements or input batch ordering."""
    now = datetime(2026, 9, 4, 11, 15, tzinfo=UTC)
    ensure_partitioned_raw_storage(engine, now=now, migrate_existing=False)
    repo = RecorderRepository()

    def batch(prefix: str) -> list[RawEvent]:
        def raw(event_type: str, ident: str, source: str = "bybit") -> RawEvent:
            return RawEvent.build(
                source=source, stream="linear", instrument="BTCUSDT",
                event_type=event_type, source_timestamp=now, received_at=now,
                sequence=prefix + ident,
                payload={"id": prefix + ident, "kind": event_type},
            )

        trade = raw("trade", "t")
        return [
            trade, trade,
            raw("orderbook_delta", "book"),
            raw("liquidation", "liquid"),
            raw("ticker", "ticker"),
            raw("trade", "coinbase", source="coinbase"),
        ]

    sql_seen: list[str] = []

    def capture(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        sql_seen.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        with engine.begin() as connection:
            assert repo.insert_events(connection, batch("baseline")) == 5
        normal_sql = tuple(sql_seen)
        sql_seen.clear()
        attributed = insert_same_batch_attributed_offline(engine, batch("optin"))
        attributed_sql = tuple(sql_seen)
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert len(normal_sql) == 3  # relkind, dedupe RETURNING, raw INSERT
    assert attributed_sql == normal_sql
    assert sum("RETURNING dedupe_key, id" in sql for sql in normal_sql) == 1
    assert attributed["additional_db_statements_vs_normal_insert"] == 0
    assert attributed["status"] == "LOCAL_TEST_TRANSACTION_COMMITTED_NO_GO"
    assert attributed["attempted_by_class"] == {
        "trade": 2, "orderbook": 1, "liquidation": 1,
        "other": 0, "excluded_non_bulk": 2,
    }
    assert attributed["inserted_by_class"] == {
        "trade": 1, "orderbook": 1, "liquidation": 1,
        "other": 0, "excluded_non_bulk": 2,
    }
    assert attributed["deduplicated_by_class"]["trade"] == 1
    assert attributed["real_local_db_commit_observed"]
    assert not attributed["production_execution_authorized"]
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM raw_market_events")
        ).scalar_one() == 10

    repeat = insert_same_batch_attributed_offline(engine, batch("optin"))
    assert all(count == 0 for count in repeat["inserted_by_class"].values())
    assert repeat["deduplicated_by_class"]["trade"] == 2


def test_b1_same_batch_claim_extraction_rolls_back_both_tables(engine) -> None:
    """A failure AFTER the raw INSERT cannot produce a committed report."""
    now = datetime(2026, 9, 4, 11, 15, tzinfo=UTC)
    ensure_partitioned_raw_storage(engine, now=now, migrate_existing=False)

    class FailsAfterRawInsert(RecorderRepository):
        @staticmethod
        def _insert_partitioned_events(
            connection, rows, *, claimed_dedupe_keys=None
        ):
            result = RecorderRepository._insert_partitioned_events(
                connection, rows, claimed_dedupe_keys=claimed_dedupe_keys
            )
            assert result == 1
            raise RuntimeError("offline forced post-insert failure")

    raw = RawEvent.build(
        source="bybit", stream="linear", instrument="BTCUSDT",
        event_type="trade", source_timestamp=now, received_at=now,
        payload={"kind": "trade"},
    )
    with pytest.raises(RuntimeError, match="post-insert failure"):
        insert_same_batch_attributed_offline(
            engine, [raw], repository=FailsAfterRawInsert()
        )
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM raw_market_events")
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT count(*) FROM raw_event_dedupe")
        ).scalar_one() == 0


def test_b1_claim_receipt_rejects_legacy_PostgreSQL_before_write(engine) -> None:
    """The new repository opt-in must not fall back to a changed SQL path."""
    raw = RawEvent.build(
        source="bybit", stream="linear", instrument="BTCUSDT",
        event_type="trade",
        source_timestamp=datetime(2026, 9, 4, 11, tzinfo=UTC),
        received_at=datetime(2026, 9, 4, 11, tzinfo=UTC),
        payload={"kind": "trade"},
    )
    with engine.begin() as connection:
        with pytest.raises(ValueError, match="partitioned PostgreSQL"):
            RecorderRepository().insert_partitioned_events_with_claim_keys(
                connection, [raw]
            )
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM raw_market_events")
        ).scalar_one() == 0


def test_b1_same_batch_rejects_cross_category_duplicate_key_before_sql(engine) -> None:
    now = datetime(2026, 9, 4, 11, 15, tzinfo=UTC)
    ensure_partitioned_raw_storage(engine, now=now, migrate_existing=False)
    trade = RawEvent.build(
        source="bybit", stream="linear", instrument="BTCUSDT",
        event_type="trade", source_timestamp=now, received_at=now,
        payload={"kind": "trade"},
    )
    book = RawEvent.build(
        source="bybit", stream="linear", instrument="BTCUSDT",
        event_type="orderbook_delta", source_timestamp=now, received_at=now,
        payload={"kind": "orderbook"},
    ).model_copy(update={"dedupe_key": trade.dedupe_key})
    statements: list[str] = []

    def capture(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        with pytest.raises(ValueError, match="cross-category"):
            insert_same_batch_attributed_offline(engine, [trade, book])
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert statements == []


def test_b1_same_batch_rejects_unexpected_claim_key_and_rolls_back(engine) -> None:
    now = datetime(2026, 9, 4, 11, 15, tzinfo=UTC)
    ensure_partitioned_raw_storage(engine, now=now, migrate_existing=False)

    class WrongClaim(RecorderRepository):
        def insert_partitioned_events_with_claim_keys(self, connection, events):
            inserted, _keys = super().insert_partitioned_events_with_claim_keys(
                connection, events
            )
            return inserted, frozenset({"unexpected-private-key"})

    raw = RawEvent.build(
        source="bybit", stream="linear", instrument="BTCUSDT",
        event_type="trade", source_timestamp=now, received_at=now,
        payload={"kind": "trade"},
    )
    with pytest.raises(RuntimeError, match="unexpected dedupe key"):
        insert_same_batch_attributed_offline(
            engine, [raw], repository=WrongClaim()
        )
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM raw_market_events")
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT count(*) FROM raw_event_dedupe")
        ).scalar_one() == 0




def test_b1_same_batch_concurrent_duplicate_replay_callback_failure_keeps_commit(
    engine,
) -> None:
    """Two local PG writers race on the same batch, not on a fake database."""
    now = datetime(2026, 9, 4, 11, 15, tzinfo=UTC)
    ensure_partitioned_raw_storage(engine, now=now, migrate_existing=False)

    def bulk(event_type: str, sequence: str) -> RawEvent:
        return RawEvent.build(
            source="bybit",
            stream="linear",
            instrument="BTCUSDT",
            event_type=event_type,
            source_timestamp=now,
            received_at=now,
            sequence=sequence,
            payload={"fixture_only": sequence},
        )

    trade = bulk("trade", "shared-trade")
    events = [
        trade, trade,
        bulk("orderbook_delta", "shared-orderbook"),
        bulk("liquidation", "shared-liquidation"),
    ]
    barrier = Barrier(2)
    callback_receipts: list[tuple[int, dict[str, int], int]] = []

    def write_once(index: int) -> dict[str, object]:
        def after_commit(counts) -> None:
            with engine.connect() as reader:
                # The other competing transaction must never surface a
                # callback before its own claimed rows are committed.
                committed_rows = reader.execute(
                    text("SELECT count(*) FROM raw_market_events")
                ).scalar_one()
            callback_receipts.append((index, dict(counts), committed_rows))
            with pytest.raises(TypeError):
                counts["trade"] = 999
            if index == 0:
                # Notification failure occurs AFTER real local commit.
                raise RuntimeError("PRIVATE_LOCAL_CALLBACK_FAILURE")

        barrier.wait(timeout=10)
        return insert_same_batch_attributed_offline(
            engine, events, on_committed_counts=after_commit
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(write_once, index) for index in range(2)]
        reports = [future.result(timeout=15) for future in futures]

    assert all(
        report["status"] == "LOCAL_TEST_TRANSACTION_COMMITTED_NO_GO"
        and not report["production_execution_authorized"]
        and report["additional_db_statements_vs_normal_insert"] == 0
        for report in reports
    )
    assert [report["postcommit_notification_status"] for report in reports] == [
        "FAILED_AFTER_COMMIT", "DELIVERED"
    ]
    assert sorted(sum(report["inserted_by_class"].values()) for report in reports) == [
        0, 3
    ]
    for category in ("trade", "orderbook", "liquidation"):
        assert sum(report["inserted_by_class"][category] for report in reports) == 1
    assert all(report["attempted_by_class"]["trade"] == 2 for report in reports)
    assert len(callback_receipts) == 2
    assert sorted(index for index, _, _ in callback_receipts) == [0, 1]
    assert all(committed_rows == 3 for _, _, committed_rows in callback_receipts)
    assert sum(sum(counts.values()) for _, counts, _ in callback_receipts) == 3

    with engine.connect() as reader:
        assert reader.execute(
            text("SELECT count(*) FROM raw_market_events")
        ).scalar_one() == 3
        assert reader.execute(
            text("SELECT count(*) FROM raw_event_dedupe")
        ).scalar_one() == 3
    assert "PRIVATE_LOCAL_CALLBACK_FAILURE" not in str(reports)
    assert all(
        event.dedupe_key not in str(reports) for event in events
    )




def test_b1_concurrent_rollback_replay_has_no_orphan_dedupe_claims(engine) -> None:
    """An uncommitted writer must not strand dedupe claims during a rival replay."""
    now = datetime(2026, 9, 4, 11, 15, tzinfo=UTC)
    ensure_partitioned_raw_storage(engine, now=now, migrate_existing=False)

    def bulk(kind: str, sequence: str) -> RawEvent:
        return RawEvent.build(
            source="bybit", stream="linear", instrument="BTCUSDT",
            event_type=kind, source_timestamp=now, received_at=now,
            sequence=sequence, payload={"local_only": sequence},
        )

    trade = bulk("trade", "shared-trade")
    book = bulk("orderbook_delta", "shared-book")
    liquidation = bulk("liquidation", "shared-liquidation")
    failing_only = bulk("trade", "rolled-back-only")
    succeeding_only = bulk("orderbook_delta", "committed-only")
    failed_batch = [trade, trade, book, liquidation, failing_only]
    successful_batch = [trade, trade, book, liquidation, succeeding_only]
    failed_has_inserted = Event()
    rival_attempting_insert = Event()
    callbacks: list[dict[str, int]] = []

    class RollBackAfterRawInsert(RecorderRepository):
        def insert_partitioned_events_with_claim_keys(self, connection, events):
            claimed = super().insert_partitioned_events_with_claim_keys(
                connection, events
            )
            assert claimed[0] == 4
            # Raw rows and dedupe claims exist INSIDE the open transaction.
            failed_has_inserted.set()
            if not rival_attempting_insert.wait(timeout=10):
                raise AssertionError("rival local PostgreSQL writer never arrived")
            raise RuntimeError("fixed local rollback after raw insert")

    class SignalCompetingWriter(RecorderRepository):
        def insert_partitioned_events_with_claim_keys(self, connection, events):
            rival_attempting_insert.set()
            return super().insert_partitioned_events_with_claim_keys(
                connection, events
            )

    def write_failing() -> None:
        # Its callback MUST NOT run when its transaction rolls back.
        insert_same_batch_attributed_offline(
            engine, failed_batch, repository=RollBackAfterRawInsert(),
            on_committed_counts=lambda _counts: pytest.fail(
                "callback for rolled-back local transaction"
            ),
        )

    def write_succeeding() -> dict[str, object]:
        def on_commit(counts) -> None:
            with engine.connect() as reader:
                assert reader.execute(
                    text("SELECT count(*) FROM raw_market_events")
                ).scalar_one() == 4
            with pytest.raises(TypeError):
                counts["trade"] = 999
            callbacks.append(dict(counts))

        return insert_same_batch_attributed_offline(
            engine, successful_batch, repository=SignalCompetingWriter(),
            on_committed_counts=on_commit,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        failing = executor.submit(write_failing)
        assert failed_has_inserted.wait(timeout=10)
        succeeding = executor.submit(write_succeeding)
        result = succeeding.result(timeout=15)
        with pytest.raises(RuntimeError, match="fixed local rollback"):
            failing.result(timeout=15)

    assert result["status"] == "LOCAL_TEST_TRANSACTION_COMMITTED_NO_GO"
    assert result["postcommit_notification_status"] == "DELIVERED"
    assert result["attempted_by_class"]["trade"] == 2
    assert result["deduplicated_by_class"]["trade"] == 1
    assert result["inserted_by_class"] == {
        "trade": 1, "orderbook": 2, "liquidation": 1,
        "other": 0, "excluded_non_bulk": 0,
    }
    assert callbacks == [result["inserted_by_class"]]
    assert result["additional_db_statements_vs_normal_insert"] == 0
    assert result["production_execution_authorized"] is False
    assert result["production_insert_attribution_proven"] is False
    with engine.connect() as reader:
        assert reader.execute(
            text("SELECT count(*) FROM raw_market_events")
        ).scalar_one() == 4
        assert reader.execute(
            text("SELECT count(*) FROM raw_event_dedupe")
        ).scalar_one() == 4
        for table in ("raw_market_events", "raw_event_dedupe"):
            assert reader.execute(
                text(f"SELECT count(*) FROM {table} WHERE dedupe_key = :key"),
                {"key": failing_only.dedupe_key},
            ).scalar_one() == 0
            assert reader.execute(
                text(f"SELECT count(*) FROM {table} WHERE dedupe_key = :key"),
                {"key": succeeding_only.dedupe_key},
            ).scalar_one() == 1
    assert all(
        raw.dedupe_key not in str(result) for raw in successful_batch
    )



def test_b1_postcommit_hook_cannot_run_before_commit_and_returns_only_counts(
    engine,
) -> None:
    now = datetime(2026, 9, 4, 11, 15, tzinfo=UTC)
    ensure_partitioned_raw_storage(engine, now=now, migrate_existing=False)
    calls: list[dict[str, int]] = []

    def callback(counts) -> None:
        # A separate connection must see committed rows before notification.
        with engine.connect() as reader:
            assert reader.execute(
                text("SELECT count(*) FROM raw_market_events")
            ).scalar_one() == 1
        with pytest.raises(TypeError):
            counts["trade"] = 999
        calls.append(dict(counts))

    event_row = RawEvent.build(
        source="bybit", stream="linear", instrument="BTCUSDT",
        event_type="trade", source_timestamp=now, received_at=now,
        payload={"secret": "PRIVATE_PAYLOAD_MUST_NOT_ESCAPE"},
    )
    result = insert_same_batch_attributed_offline(
        engine, [event_row, event_row], on_committed_counts=callback
    )
    assert result["postcommit_notification_status"] == "DELIVERED"
    assert len(calls) == 1
    assert calls[0]["trade"] == 1
    assert result["deduplicated_by_class"]["trade"] == 1
    assert result["production_execution_authorized"] is False
    assert "PRIVATE_PAYLOAD" not in str(result)
    assert event_row.dedupe_key not in str(result)

    repeat = insert_same_batch_attributed_offline(
        engine, [event_row], on_committed_counts=callback_noop
    )
    assert repeat["inserted_by_class"]["trade"] == 0
    assert repeat["postcommit_notification_status"] == "DELIVERED"


def callback_noop(_counts) -> None:
    return None


def test_b1_postcommit_callback_failure_cannot_roll_back_committed_rows(engine) -> None:
    now = datetime(2026, 9, 4, 11, 15, tzinfo=UTC)
    ensure_partitioned_raw_storage(engine, now=now, migrate_existing=False)
    row = RawEvent.build(
        source="bybit", stream="linear", instrument="BTCUSDT",
        event_type="trade", source_timestamp=now, received_at=now,
        payload={"secret": "SENSITIVE_CALLBACK_ERROR"},
    )

    def broken(_counts) -> None:
        raise ValueError("SENSITIVE_CALLBACK_ERROR")

    result = insert_same_batch_attributed_offline(
        engine, [row], on_committed_counts=broken
    )
    assert result["postcommit_notification_status"] == "FAILED_AFTER_COMMIT"
    assert result["inserted_by_class"]["trade"] == 1
    assert "SENSITIVE_CALLBACK_ERROR" not in str(result)
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM raw_market_events")
        ).scalar_one() == 1


def test_b1_postcommit_callback_never_runs_after_failed_transaction(engine) -> None:
    now = datetime(2026, 9, 4, 11, 15, tzinfo=UTC)
    ensure_partitioned_raw_storage(engine, now=now, migrate_existing=False)
    calls: list[dict[str, int]] = []

    class FailsAfterInsert(RecorderRepository):
        def insert_partitioned_events_with_claim_keys(self, connection, events):
            super().insert_partitioned_events_with_claim_keys(connection, events)
            raise RuntimeError("forced rollback after raw insert")

    row = RawEvent.build(
        source="bybit", stream="linear", instrument="BTCUSDT",
        event_type="orderbook_delta", source_timestamp=now, received_at=now,
        payload={"kind": "orderbook"},
    )
    with pytest.raises(RuntimeError, match="forced rollback"):
        insert_same_batch_attributed_offline(
            engine, [row], repository=FailsAfterInsert(),
            on_committed_counts=lambda counts: calls.append(dict(counts))
        )
    assert calls == []
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM raw_market_events")
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT count(*) FROM raw_event_dedupe")
        ).scalar_one() == 0


def test_b1_postcommit_rejects_async_or_invalid_observer_without_SQL(engine) -> None:
    now = datetime(2026, 9, 4, 11, 15, tzinfo=UTC)
    ensure_partitioned_raw_storage(engine, now=now, migrate_existing=False)
    row = RawEvent.build(
        source="bybit", stream="linear", instrument="BTCUSDT",
        event_type="trade", source_timestamp=now, received_at=now,
        payload={"kind": "trade"},
    )

    async def asynchronous(_counts):
        return None

    statements: list[str] = []

    def capture(_conn, _cursor, statement, _params, _ctx, _many) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        for invalid in (asynchronous, 5):
            with pytest.raises(ValueError, match="synchronous"):
                insert_same_batch_attributed_offline(
                    engine, [row], on_committed_counts=invalid
                )
        assert statements == []
    finally:
        event.remove(engine, "before_cursor_execute", capture)


def test_b1_postcommit_hook_return_value_is_not_claimed_delivered(engine) -> None:
    now = datetime(2026, 9, 4, 11, 15, tzinfo=UTC)
    ensure_partitioned_raw_storage(engine, now=now, migrate_existing=False)
    row = RawEvent.build(
        source="bybit", stream="linear", instrument="BTCUSDT",
        event_type="trade", source_timestamp=now, received_at=now,
        payload={"kind": "trade"},
    )
    result = insert_same_batch_attributed_offline(
        engine, [row], on_committed_counts=lambda _counts: "unexpected data"
    )
    assert result["postcommit_notification_status"] == "INVALID_CALLBACK_RETURN"
    assert result["real_local_db_commit_observed"]


def test_b1_attribution_benchmark_local_pg_non_gating(engine) -> None:
    """No performance threshold; this is CI-local comparative evidence only."""
    import json

    now = datetime(2026, 9, 4, 11, 15, tzinfo=UTC)
    ensure_partitioned_raw_storage(engine, now=now, migrate_existing=False)
    result = compare_local_postcommit_overhead(engine)
    assert result["status"] == "LOCAL_ONLY_NOT_PRODUCTION_ACCEPTANCE"
    assert result["transaction_count"] == 18
    assert [v["batch_size"] for v in result["metrics"]] == [8, 32, 64]
    for batch in result["metrics"]:
        assert batch["repetitions_per_arm"] == 3
        for arm in ("baseline_medians", "postcommit_medians"):
            assert batch[arm]["wall_ns"] > 0
            assert batch[arm]["cpu_ns"] > 0
            assert batch[arm]["python_peak_bytes"] > 0
    assert result["production_overhead_accepted"] is False
    assert result["production_execution_authorized"] is False
    print("B1_ATTRIBUTION_BENCHMARK_JSON=" + json.dumps(result, sort_keys=True))
