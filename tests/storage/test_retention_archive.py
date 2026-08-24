import gzip
import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, delete, select

from bp_engine.recorder.models import RawEvent
from bp_engine.recorder.state import MarketStateSnapshot
from bp_engine.storage.maintenance import (
    ArchiveVerificationError,
    archive_interval,
    delete_verified_interval,
    prune_expired_archives,
    prune_expired_state,
    verify_archive,
)
from bp_engine.storage.recorder import RecorderRepository
from bp_engine.storage.schema import market_state_1s, metadata, raw_market_events


def event(received_at: datetime, sequence: int) -> RawEvent:
    return RawEvent.build(
        source="bybit",
        stream="spot",
        instrument="BTCUSDT",
        event_type="trade",
        source_timestamp=received_at,
        received_at=received_at,
        sequence=sequence,
        payload={"price": str(64_000 + sequence), "sequence": sequence},
    )


def seeded_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'archive-test.db'}")
    metadata.create_all(engine)
    repository = RecorderRepository()
    start = datetime(2026, 8, 23, 10, 0, tzinfo=UTC)
    events = [
        event(start - timedelta(seconds=1), 1),
        event(start + timedelta(minutes=5), 2),
        event(start + timedelta(minutes=55), 3),
        event(start + timedelta(hours=1), 4),
    ]
    with engine.begin() as connection:
        repository.insert_events(connection, events)
    return engine, start


def archive_files(archive_dir, manifest):
    archive_path = archive_dir / manifest.archive_name
    manifest_path = archive_dir / f"{manifest.archive_name}.manifest.json"
    return archive_path, manifest_path


def interval_sequences(engine, start, end):
    with engine.connect() as connection:
        return connection.execute(
            select(raw_market_events.c.sequence)
            .where(raw_market_events.c.received_at >= start)
            .where(raw_market_events.c.received_at < end)
            .order_by(raw_market_events.c.id)
        ).scalars().all()


def state_snapshot(source, stream, instrument, at, *, suffix=""):
    return MarketStateSnapshot(
        bucket_at=at,
        state_key=f"{source}/{stream}/{instrument}{suffix}",
        source=source,
        stream=stream,
        instrument=instrument,
        last_event_at=at,
        state={"last_price": "64000"},
    )


def test_archive_interval_contains_only_requested_hour_and_verifies(tmp_path) -> None:
    engine, start = seeded_engine(tmp_path)
    archive_dir = tmp_path / "archive"

    manifest = archive_interval(
        engine,
        archive_dir,
        start,
        start + timedelta(hours=1),
    )
    archive_path, manifest_path = archive_files(archive_dir, manifest)

    assert manifest.row_count == 2
    assert manifest.start_at == start
    assert manifest.end_at == start + timedelta(hours=1)
    assert manifest.compressed_bytes == archive_path.stat().st_size
    assert manifest.sha256 == hashlib.sha256(archive_path.read_bytes()).hexdigest()
    assert verify_archive(archive_path, manifest_path) == manifest

    with gzip.open(archive_path, "rt", encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle]
    assert [row["sequence"] for row in rows] == ["2", "3"]


def test_identical_intervals_produce_identical_compressed_bytes(tmp_path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first_engine, start = seeded_engine(first_root)
    second_engine, second_start = seeded_engine(second_root)
    end = start + timedelta(hours=1)

    first = archive_interval(first_engine, first_root / "archive", start, end)
    second = archive_interval(
        second_engine,
        second_root / "archive",
        second_start,
        second_start + timedelta(hours=1),
    )
    first_path, _ = archive_files(first_root / "archive", first)
    second_path, _ = archive_files(second_root / "archive", second)

    assert first.sha256 == second.sha256
    assert first_path.read_bytes() == second_path.read_bytes()


def test_verify_archive_rejects_corrupt_compressed_bytes(tmp_path) -> None:
    engine, start = seeded_engine(tmp_path)
    archive_dir = tmp_path / "archive"
    manifest = archive_interval(engine, archive_dir, start, start + timedelta(hours=1))
    archive_path, manifest_path = archive_files(archive_dir, manifest)

    archive_path.write_bytes(archive_path.read_bytes() + b"tamper")

    with pytest.raises(ArchiveVerificationError, match="SHA-256"):
        verify_archive(archive_path, manifest_path)


def test_existing_verified_archive_is_reused_after_partial_database_deletion(tmp_path) -> None:
    engine, start = seeded_engine(tmp_path)
    archive_dir = tmp_path / "archive"
    first = archive_interval(engine, archive_dir, start, start + timedelta(hours=1))
    archive_path, _ = archive_files(archive_dir, first)
    original_bytes = archive_path.read_bytes()

    with engine.begin() as connection:
        row_id = connection.execute(
            select(raw_market_events.c.id)
            .where(raw_market_events.c.received_at >= start.replace(tzinfo=None))
            .where(
                raw_market_events.c.received_at
                < (start + timedelta(hours=1)).replace(tzinfo=None)
            )
            .order_by(raw_market_events.c.id)
            .limit(1)
        ).scalar_one()
        connection.execute(delete(raw_market_events).where(raw_market_events.c.id == row_id))

    second = archive_interval(engine, archive_dir, start, start + timedelta(hours=1))

    assert second == first
    assert archive_path.read_bytes() == original_bytes
    with gzip.open(archive_path, "rt", encoding="utf-8") as handle:
        assert sum(1 for _ in handle) == 2


def test_missing_archive_prevents_raw_deletion(tmp_path) -> None:
    engine, start = seeded_engine(tmp_path)
    end = start + timedelta(hours=1)
    before = interval_sequences(engine, start, end)

    with pytest.raises(ArchiveVerificationError, match="missing"):
        delete_verified_interval(
            engine,
            tmp_path / "missing.jsonl.gz",
            tmp_path / "missing.jsonl.gz.manifest.json",
            batch_size=1,
        )

    assert interval_sequences(engine, start, end) == before


def test_corrupt_archive_prevents_raw_deletion(tmp_path) -> None:
    engine, start = seeded_engine(tmp_path)
    end = start + timedelta(hours=1)
    archive_dir = tmp_path / "archive"
    manifest = archive_interval(engine, archive_dir, start, end)
    archive_path, manifest_path = archive_files(archive_dir, manifest)
    archive_path.write_bytes(archive_path.read_bytes() + b"tamper")
    before = interval_sequences(engine, start, end)

    with pytest.raises(ArchiveVerificationError, match="SHA-256"):
        delete_verified_interval(engine, archive_path, manifest_path, batch_size=1)

    assert interval_sequences(engine, start, end) == before


def test_verified_archive_allows_bounded_idempotent_raw_deletion(tmp_path) -> None:
    engine, start = seeded_engine(tmp_path)
    end = start + timedelta(hours=1)
    archive_dir = tmp_path / "archive"
    manifest = archive_interval(engine, archive_dir, start, end)
    archive_path, manifest_path = archive_files(archive_dir, manifest)

    with engine.begin() as connection:
        row_id = connection.execute(
            select(raw_market_events.c.id)
            .where(raw_market_events.c.received_at >= start)
            .where(raw_market_events.c.received_at < end)
            .order_by(raw_market_events.c.id)
            .limit(1)
        ).scalar_one()
        connection.execute(delete(raw_market_events).where(raw_market_events.c.id == row_id))

    assert delete_verified_interval(engine, archive_path, manifest_path, batch_size=1) == 1
    assert delete_verified_interval(engine, archive_path, manifest_path, batch_size=1) == 0
    assert interval_sequences(engine, start, end) == []
    assert interval_sequences(engine, start - timedelta(seconds=1), start) == ["1"]
    assert interval_sequences(engine, end, end + timedelta(seconds=1)) == ["4"]


def test_archive_pruning_waits_for_all_required_compact_feeds(tmp_path) -> None:
    engine, start = seeded_engine(tmp_path)
    end = start + timedelta(hours=1)
    archive_dir = tmp_path / "archive"
    manifest = archive_interval(engine, archive_dir, start, end)
    archive_path, manifest_path = archive_files(archive_dir, manifest)
    repository = RecorderRepository()
    advanced_at = end + timedelta(seconds=30)

    three_feeds = [
        state_snapshot("bybit", "spot", "BTCUSDT", advanced_at),
        state_snapshot("bybit", "linear", "BTCUSDT", advanced_at),
        state_snapshot("coinbase", "spot", "BTC-USD", advanced_at),
    ]
    with engine.begin() as connection:
        repository.upsert_state_snapshots(connection, three_feeds)

    removed = prune_expired_archives(
        engine,
        archive_dir,
        now=end + timedelta(hours=2),
        retention_hours=1,
    )
    assert removed == []
    assert archive_path.exists()
    assert manifest_path.exists()

    with engine.begin() as connection:
        repository.upsert_state_snapshots(
            connection,
            [state_snapshot("polymarket", "market", "BTC", advanced_at)],
        )

    removed = prune_expired_archives(
        engine,
        archive_dir,
        now=end + timedelta(hours=2),
        retention_hours=1,
    )
    assert removed == []
    assert archive_path.exists()
    assert manifest_path.exists()

    assert delete_verified_interval(
        engine,
        archive_path,
        manifest_path,
        batch_size=1,
    ) == 2

    removed = prune_expired_archives(
        engine,
        archive_dir,
        now=end + timedelta(hours=2),
        retention_hours=1,
    )
    assert removed == [manifest.archive_name]
    assert not archive_path.exists()
    assert not manifest_path.exists()


def test_state_pruning_removes_only_expired_compact_rows(tmp_path) -> None:
    engine, start = seeded_engine(tmp_path)
    repository = RecorderRepository()
    now = start + timedelta(days=100)
    old_at = now - timedelta(days=91)
    recent_at = now - timedelta(days=1)
    with engine.begin() as connection:
        repository.upsert_state_snapshots(
            connection,
            [
                state_snapshot("bybit", "spot", "BTCUSDT", old_at, suffix="/old"),
                state_snapshot("bybit", "spot", "BTCUSDT", recent_at, suffix="/recent"),
            ],
        )

    assert prune_expired_state(engine, now=now, retention_days=90, batch_size=1) == 1

    with engine.connect() as connection:
        remaining = connection.execute(
            select(market_state_1s.c.state_key).order_by(market_state_1s.c.state_key)
        ).scalars().all()
    assert remaining == ["bybit/spot/BTCUSDT/recent"]
