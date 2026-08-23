import gzip
import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from bp_engine.storage.maintenance import (
    ArchiveVerificationError,
    archive_interval,
    verify_archive,
)
from sqlalchemy import create_engine, delete, select

from bp_engine.recorder.models import RawEvent
from bp_engine.storage.recorder import RecorderRepository
from bp_engine.storage.schema import metadata, raw_market_events


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
