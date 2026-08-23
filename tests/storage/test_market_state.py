from datetime import UTC, datetime

from sqlalchemy import create_engine, select

from bp_engine.recorder.state import MarketStateSnapshot
from bp_engine.storage.recorder import RecorderRepository
from bp_engine.storage.schema import market_state_1s, metadata


def test_upsert_state_snapshots_updates_same_bucket_and_state_key() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    repository = RecorderRepository()
    bucket = datetime(2026, 8, 23, 20, 12, 57, tzinfo=UTC)

    first = MarketStateSnapshot(
        bucket_at=bucket,
        state_key="bybit/spot/BTCUSDT",
        source="bybit",
        stream="spot",
        instrument="BTCUSDT",
        market_id=None,
        asset_id=None,
        last_event_at=datetime(2026, 8, 23, 20, 12, 57, 100_000, tzinfo=UTC),
        state={"best_bid": "64000.0", "best_ask": "64000.5"},
    )
    updated = MarketStateSnapshot(
        bucket_at=bucket,
        state_key="bybit/spot/BTCUSDT",
        source="bybit",
        stream="spot",
        instrument="BTCUSDT",
        market_id=None,
        asset_id=None,
        last_event_at=datetime(2026, 8, 23, 20, 12, 57, 900_000, tzinfo=UTC),
        state={"best_bid": "64000.2", "best_ask": "64000.6"},
    )

    with engine.begin() as connection:
        assert repository.upsert_state_snapshots(connection, [first]) == 1
        repository.upsert_state_snapshots(connection, [updated])
        rows = connection.execute(select(market_state_1s)).mappings().all()

    assert len(rows) == 1
    assert rows[0]["bucket_at"] == bucket.replace(tzinfo=None)
    assert rows[0]["last_event_at"] == updated.last_event_at.replace(tzinfo=None)
    assert rows[0]["state"] == {"best_bid": "64000.2", "best_ask": "64000.6"}


def test_market_state_snapshot_requires_timezone_aware_times() -> None:
    bucket = datetime(2026, 8, 23, 20, 12, 57)

    try:
        MarketStateSnapshot(
            bucket_at=bucket,
            state_key="coinbase/spot/BTC-USD",
            source="coinbase",
            stream="spot",
            instrument="BTC-USD",
            market_id=None,
            asset_id=None,
            last_event_at=datetime(2026, 8, 23, 20, 12, 57, tzinfo=UTC),
            state={},
        )
    except ValueError as exc:
        assert "timezone-aware" in str(exc)
    else:
        raise AssertionError("naive bucket_at should be rejected")
