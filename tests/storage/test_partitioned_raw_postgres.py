from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from sqlalchemy import create_engine, insert, text
from sqlalchemy.exc import DBAPIError

from bp_engine.config import Settings
from bp_engine.recorder.models import RawEvent
from bp_engine.recorder.state import MarketStateSnapshot
from bp_engine.storage.maintenance import (
    ArchiveVerificationError,
    archive_interval,
    build_composite_storage_health,
    retire_verified_partition,
)
from bp_engine.storage.partitioned_raw import (
    RawStorageMode,
    drop_raw_partition,
    ensure_hour_partitions,
    ensure_partitioned_raw_storage,
    list_raw_partitions,
    raw_storage_mode,
)
from bp_engine.storage.recorder import RecorderRepository
from bp_engine.storage.schema import (
    market_state_1s,
    raw_market_events,
    storage_maintenance_runs,
)

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
