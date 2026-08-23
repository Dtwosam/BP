import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine

from bp_engine.recorder.models import RawEvent
from bp_engine.storage.recorder import RecorderRepository
from bp_engine.storage.schema import metadata


def event(source: str, stream: str, received_at: datetime, sequence: str) -> RawEvent:
    return RawEvent.build(
        source=source,
        stream=stream,
        instrument="BTC",
        event_type="trade",
        source_timestamp=received_at,
        received_at=received_at,
        sequence=sequence,
        payload={"sequence": sequence},
    )


def test_soak_report_cli_accepts_fixed_end_time(tmp_path) -> None:
    database_path = tmp_path / "soak.db"
    database_url = f"sqlite+pysqlite:///{database_path}"
    engine = create_engine(database_url)
    metadata.create_all(engine)
    repo = RecorderRepository()

    end = datetime(2026, 8, 23, 20, 12, 57, tzinfo=UTC)
    start = end - timedelta(hours=1)
    feeds = [
        ("polymarket", "market"),
        ("bybit", "spot"),
        ("bybit", "linear"),
        ("coinbase", "spot"),
    ]
    with engine.begin() as connection:
        repo.insert_events(
            connection,
            [
                event(source, stream, start + timedelta(minutes=1), str(index))
                for index, (source, stream) in enumerate(feeds, start=1)
            ],
        )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/soak_report.py",
            "--database-url",
            database_url,
            "--hours",
            "1",
            "--minimum-hours",
            "1",
            "--end-at",
            "2026-08-23T20:12:57Z",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["end_at"] == "2026-08-23T20:12:57Z"
    assert report["passed"] is True
