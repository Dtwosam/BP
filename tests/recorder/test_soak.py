from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine

from bp_engine.recorder.models import FeedIncident, RawEvent
from bp_engine.recorder.soak import FeedKey, build_soak_report
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


def setup_db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    return engine, RecorderRepository()


def test_soak_report_passes_when_all_feeds_have_events_and_stale_recovers() -> None:
    engine, repo = setup_db()
    start = datetime(2026, 8, 20, 20, 0, tzinfo=UTC)
    end = start + timedelta(hours=1)
    required = [FeedKey("polymarket", "market"), FeedKey("bybit", "spot")]

    with engine.begin() as connection:
        repo.insert_events(
            connection,
            [
                event("polymarket", "market", start + timedelta(minutes=1), "1"),
                event("bybit", "spot", start + timedelta(minutes=1), "2"),
            ],
        )
        repo.record_incident(
            connection,
            FeedIncident(
                source="bybit",
                stream="spot",
                incident_type="stale",
                observed_at=start + timedelta(minutes=10),
                details={"age_seconds": 11},
            ),
        )
        repo.record_incident(
            connection,
            FeedIncident(
                source="bybit",
                stream="spot",
                incident_type="recovered",
                observed_at=start + timedelta(minutes=11),
                details={},
            ),
        )
        report = build_soak_report(
            connection,
            start_at=start,
            end_at=end,
            required_feeds=required,
            minimum_duration_seconds=3600,
        )

    assert report.passed is True
    assert report.failures == []
    assert report.feeds["bybit/spot"].event_count == 1
    assert report.incidents["bybit/spot"]["stale"] == 1


def test_soak_report_fails_missing_feed_unresolved_stale_and_backpressure() -> None:
    engine, repo = setup_db()
    start = datetime(2026, 8, 20, 20, 0, tzinfo=UTC)
    end = start + timedelta(hours=1)
    required = [FeedKey("polymarket", "market"), FeedKey("coinbase", "spot")]

    with engine.begin() as connection:
        repo.insert_events(
            connection,
            [event("polymarket", "market", start + timedelta(minutes=1), "1")],
        )
        for incident_type in ("stale", "backpressure"):
            repo.record_incident(
                connection,
                FeedIncident(
                    source="polymarket",
                    stream="market",
                    incident_type=incident_type,
                    observed_at=start + timedelta(minutes=10),
                    details={},
                ),
            )
        report = build_soak_report(
            connection,
            start_at=start,
            end_at=end,
            required_feeds=required,
            minimum_duration_seconds=3600,
        )

    assert report.passed is False
    assert any("coinbase/spot has no events" in failure for failure in report.failures)
    assert any("polymarket/market has unresolved stale" in failure for failure in report.failures)
    assert any("polymarket/market recorded backpressure" in failure for failure in report.failures)


def test_soak_report_rejects_window_shorter_than_required_duration() -> None:
    engine, _ = setup_db()
    start = datetime(2026, 8, 20, 20, 0, tzinfo=UTC)
    end = start + timedelta(minutes=30)

    with engine.connect() as connection:
        report = build_soak_report(
            connection,
            start_at=start,
            end_at=end,
            required_feeds=[],
            minimum_duration_seconds=3600,
        )

    assert report.passed is False
    assert report.failures == ["window duration 1800s is below required 3600s"]
