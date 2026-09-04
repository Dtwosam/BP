import json
import subprocess
import sys
from collections import namedtuple
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine

from bp_engine.config import Settings
from bp_engine.recorder.models import RawEvent
from bp_engine.recorder.state import MarketStateSnapshot
from bp_engine.storage.maintenance import build_storage_report, project_raw_bytes_per_day
from bp_engine.storage.recorder import RecorderRepository
from bp_engine.storage.schema import metadata

DiskUsage = namedtuple("DiskUsage", "total used free")
GIB = 1024**3


def raw_event(at: datetime, sequence: int) -> RawEvent:
    return RawEvent.build(
        source="bybit",
        stream="spot",
        instrument="BTCUSDT",
        event_type="trade",
        source_timestamp=at,
        received_at=at,
        sequence=sequence,
        payload={"price": str(64_000 + sequence)},
    )


def state(at: datetime, key: str) -> MarketStateSnapshot:
    return MarketStateSnapshot(
        bucket_at=at,
        state_key=key,
        source="bybit",
        stream="spot",
        instrument="BTCUSDT",
        last_event_at=at,
        state={"best_bid": "64000"},
    )


def test_storage_report_summarizes_sqlite_data_and_archives(tmp_path) -> None:
    database_path = tmp_path / "storage.db"
    database_url = f"sqlite+pysqlite:///{database_path}"
    engine = create_engine(database_url)
    metadata.create_all(engine)
    repository = RecorderRepository()
    now = datetime(2026, 8, 23, 22, 0, tzinfo=UTC)
    first_raw = now - timedelta(hours=2)
    last_raw = now - timedelta(hours=1)
    first_state = now - timedelta(minutes=2)
    last_state = now - timedelta(minutes=1)

    with engine.begin() as connection:
        repository.insert_events(
            connection,
            [raw_event(first_raw, 1), raw_event(last_raw, 2)],
        )
        repository.upsert_state_snapshots(
            connection,
            [state(first_state, "one"), state(last_state, "two")],
        )

    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    (archive_dir / "raw-example.jsonl.gz").write_bytes(b"12345")
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        storage_archive_dir=str(archive_dir),
    )

    report = build_storage_report(
        engine,
        archive_dir,
        settings,
        disk_usage_fn=lambda path: DiskUsage(100 * GIB, 60 * GIB, 40 * GIB),
        now=now,
    )

    assert report["raw"]["count"] == 2
    assert report["raw"]["first_received_at"] == "2026-08-23T20:00:00Z"
    assert report["raw"]["last_received_at"] == "2026-08-23T21:00:00Z"
    assert report["raw"]["recent_24h_count"] == 2
    assert report["raw"]["total_bytes"] is None
    assert report["raw"]["average_bytes_per_event"] is None
    assert report["raw"]["projected_bytes_per_day"] is None
    assert report["state"]["count"] == 2
    assert report["state"]["first_bucket_at"] == "2026-08-23T21:58:00Z"
    assert report["state"]["last_bucket_at"] == "2026-08-23T21:59:00Z"
    assert report["archives"] == {"count": 1, "bytes": 5}
    assert report["disk"]["status"] == "ok"
    assert report["disk"]["free_bytes"] == 40 * GIB
    assert report["retention"]["hot_raw_hours"] == 24
    assert report["retention"]["archive_retention_hours"] == 24
    assert report["retention"]["state_retention_days"] == 90


def test_project_raw_bytes_per_day_uses_recent_rate_and_average_size() -> None:
    projected = project_raw_bytes_per_day(
        recent_event_count=1_000,
        recent_window_hours=2,
        average_bytes_per_event=500,
    )

    assert projected == 6_000_000


def test_storage_report_cli_loads_explicit_env_file(tmp_path) -> None:
    database_path = tmp_path / "cli.db"
    archive_dir = tmp_path / "cli-archive"
    env_file = tmp_path / "bp.env"
    env_file.write_text(
        "\n".join(
            [
                f"DATABASE_URL=sqlite+pysqlite:///{database_path}",
                f"STORAGE_ARCHIVE_DIR={archive_dir}",
                "STORAGE_HOT_RAW_HOURS=36",
                "STORAGE_ARCHIVE_RETENTION_HOURS=12",
                "STORAGE_STATE_RETENTION_DAYS=60",
                "STORAGE_WARNING_FREE_GIB=0",
                "STORAGE_CRITICAL_FREE_GIB=0",
                "STORAGE_DELETE_BATCH_SIZE=1234",
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/storage_maintenance.py",
            "report",
            "--env-file",
            str(env_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["retention"] == {
        "hot_raw_hours": 36,
        "archive_retention_hours": 12,
        "state_retention_days": 60,
    }
    assert report["disk"]["path"] == str(archive_dir)



def test_storage_report_uses_explicit_storage_health_path(tmp_path) -> None:
    database_path = tmp_path / "health-path.db"
    database_url = f"sqlite+pysqlite:///{database_path}"
    engine = create_engine(database_url)
    metadata.create_all(engine)
    archive_dir = tmp_path / "archive"
    health_dir = tmp_path / "data"
    archive_dir.mkdir()
    health_dir.mkdir()
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        storage_archive_dir=str(archive_dir),
        storage_health_path=str(health_dir),
    )

    observed_paths = []

    def usage(path):
        observed_paths.append(path)
        return DiskUsage(100 * GIB, 60 * GIB, 40 * GIB)

    report = build_storage_report(
        engine,
        archive_dir,
        settings,
        disk_usage_fn=usage,
        now=datetime(2026, 9, 5, 15, tzinfo=UTC),
    )

    assert report["disk"]["path"] == str(health_dir)
    assert observed_paths == [health_dir]
