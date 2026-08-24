from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine

from bp_engine.recorder.models import RawEvent
from bp_engine.recorder.state import MarketStateSnapshot
from bp_engine.storage.maintenance import archive_interval, prune_expired_archives
from bp_engine.storage.recorder import RecorderRepository
from bp_engine.storage.schema import metadata


def test_expired_archive_is_not_pruned_while_interval_still_has_raw_rows(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'restart-safety.db'}")
    metadata.create_all(engine)
    repository = RecorderRepository()
    start = datetime(2026, 8, 22, 20, 0, tzinfo=UTC)
    end = start + timedelta(hours=1)
    event_at = start + timedelta(minutes=5)

    raw_event = RawEvent.build(
        source="bybit",
        stream="spot",
        instrument="BTCUSDT",
        event_type="trade",
        source_timestamp=event_at,
        received_at=event_at,
        sequence=1,
        payload={"price": "64000"},
    )
    with engine.begin() as connection:
        repository.insert_events(connection, [raw_event])

    archive_dir = tmp_path / "archive"
    manifest = archive_interval(engine, archive_dir, start, end)
    archive_path = archive_dir / manifest.archive_name
    manifest_path = archive_dir / f"{manifest.archive_name}.manifest.json"
    original_bytes = archive_path.read_bytes()

    advanced_at = end + timedelta(seconds=30)
    snapshots = [
        MarketStateSnapshot(
            bucket_at=advanced_at,
            state_key=f"{source}/{stream}/{instrument}",
            source=source,
            stream=stream,
            instrument=instrument,
            last_event_at=advanced_at,
            state={"last_price": "64000"},
        )
        for source, stream, instrument in (
            ("bybit", "spot", "BTCUSDT"),
            ("bybit", "linear", "BTCUSDT"),
            ("coinbase", "spot", "BTC-USD"),
            ("polymarket", "market", "BTC"),
        )
    ]
    with engine.begin() as connection:
        repository.upsert_state_snapshots(connection, snapshots)

    removed = prune_expired_archives(
        engine,
        archive_dir,
        now=end + timedelta(hours=25),
        retention_hours=24,
    )

    assert removed == []
    assert archive_path.read_bytes() == original_bytes
    assert manifest_path.exists()
