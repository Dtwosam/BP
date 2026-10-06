from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, insert

from bp_engine.features.v4_models import V4FeatureTarget
from bp_engine.storage.schema import metadata, raw_market_events
from bp_engine.v4_paper.source_time_features import (
    V4_CORE_SOURCE_REQUIRED_FLAGS,
    V4SourceTimeReader,
    build_source_time_v4_features,
)
from bp_engine.v4_research.config import V4_PREDICTOR_NAMES


def _coinbase_payload(price: float) -> dict[str, object]:
    return {
        "events": [
            {
                "tickers": [
                    {
                        "product_id": "BTC-USD",
                        "price": str(price),
                    }
                ]
            }
        ]
    }


def _bybit_payload(price: float, *, linear: bool = False) -> dict[str, object]:
    data: dict[str, object] = {"lastPrice": str(price)}
    if linear:
        data.update({"fundingRate": "0.0001", "openInterest": "12345"})
    return {"data": data}


def _insert_event(
    connection,
    *,
    source: str,
    stream: str,
    instrument: str,
    requested_at: datetime,
    price: float,
    sequence: int,
    source_age_seconds: float = 0.25,
    received_lag_seconds: float = 0.1,
) -> None:
    source_at = requested_at - timedelta(seconds=source_age_seconds)
    received_at = requested_at - timedelta(seconds=received_lag_seconds)
    payload = (
        _coinbase_payload(price)
        if source == "coinbase"
        else _bybit_payload(price, linear=stream == "linear")
    )
    event_type = "ticker_update" if source == "coinbase" else "ticker"
    connection.execute(
        insert(raw_market_events).values(
            source=source,
            stream=stream,
            instrument=instrument,
            event_type=event_type,
            source_timestamp=source_at,
            received_at=received_at,
            sequence=str(sequence),
            market_id=None,
            asset_id=None,
            payload=payload,
            dedupe_key=f"event-{sequence}",
        )
    )


def _connection():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    return engine, engine.connect()


def test_source_time_reader_rejects_stale_source_even_if_received_recent() -> None:
    engine, connection = _connection()
    requested = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    try:
        _insert_event(
            connection,
            source="coinbase",
            stream="spot",
            instrument="BTC-USD",
            requested_at=requested,
            price=100.0,
            sequence=1,
            source_age_seconds=20.0,
            received_lag_seconds=0.1,
        )
        reader = V4SourceTimeReader()
        observation, evidence = reader.latest_price(
            connection,
            source="coinbase",
            stream="spot",
            instrument="BTC-USD",
            requested_at=requested,
        )
        assert observation is None
        assert evidence == ()

        _insert_event(
            connection,
            source="coinbase",
            stream="spot",
            instrument="BTC-USD",
            requested_at=requested,
            price=101.0,
            sequence=2,
        )
        observation, evidence = reader.latest_price(
            connection,
            source="coinbase",
            stream="spot",
            instrument="BTC-USD",
            requested_at=requested,
        )
        assert observation is not None
        assert float(observation.price) == 101.0
        assert len(evidence) == 1
        assert evidence[0].source_age_seconds == 0.25
    finally:
        connection.close()
        engine.dispose()


def test_source_time_reader_recovers_predecision_row_visible_on_retry() -> None:
    engine, connection = _connection()
    requested = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    reader = V4SourceTimeReader()
    try:
        observation, evidence = reader.latest_price(
            connection,
            source="bybit",
            stream="spot",
            instrument="BTCUSDT",
            requested_at=requested,
        )
        assert observation is None
        assert evidence == ()

        connection.execute(
            insert(raw_market_events).values(
                source="bybit",
                stream="spot",
                instrument="BTCUSDT",
                event_type="ticker",
                source_timestamp=requested - timedelta(seconds=0.2),
                received_at=requested - timedelta(seconds=0.1),
                sequence="retry-visible",
                market_id=None,
                asset_id=None,
                payload=_bybit_payload(100.0),
                dedupe_key="retry-visible",
            )
        )

        observation, evidence = reader.latest_price(
            connection,
            source="bybit",
            stream="spot",
            instrument="BTCUSDT",
            requested_at=requested,
        )
        assert observation is not None
        assert float(observation.price) == 100.0
        assert len(evidence) == 1
        assert evidence[0].received_at <= requested
    finally:
        connection.close()
        engine.dispose()


def test_source_time_reader_rejects_event_received_after_decision() -> None:
    engine, connection = _connection()
    requested = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    try:
        connection.execute(
            insert(raw_market_events).values(
                source="bybit",
                stream="spot",
                instrument="BTCUSDT",
                event_type="ticker",
                source_timestamp=requested - timedelta(seconds=0.2),
                received_at=requested + timedelta(seconds=0.1),
                sequence="1",
                market_id=None,
                asset_id=None,
                payload=_bybit_payload(100.0),
                dedupe_key="future-receipt",
            )
        )
        observation, evidence = V4SourceTimeReader().latest_price(
            connection,
            source="bybit",
            stream="spot",
            instrument="BTCUSDT",
            requested_at=requested,
        )
        assert observation is None
        assert evidence == ()
    finally:
        connection.close()
        engine.dispose()


def test_build_source_time_v4_features_reconstructs_frozen_predictor_schema() -> None:
    engine, connection = _connection()
    start = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    decision = start + timedelta(seconds=240)
    target = V4FeatureTarget(
        condition_id="condition-v4",
        slug="btc-updown-5m",
        horizon_seconds=300,
        market_start_at=start,
        market_end_at=start + timedelta(seconds=300),
    )
    requested_times = sorted(
        {
            start,
            decision - timedelta(seconds=120),
            decision - timedelta(seconds=60),
            decision - timedelta(seconds=30),
            decision,
            decision - timedelta(seconds=300),
            decision - timedelta(seconds=900),
            decision - timedelta(seconds=3600),
        }
    )
    sequence = 0
    try:
        for venue_index, (source, stream, instrument) in enumerate(
            (
                ("coinbase", "spot", "BTC-USD"),
                ("bybit", "spot", "BTCUSDT"),
                ("bybit", "linear", "BTCUSDT"),
            )
        ):
            for time_index, requested in enumerate(requested_times):
                sequence += 1
                _insert_event(
                    connection,
                    source=source,
                    stream=stream,
                    instrument=instrument,
                    requested_at=requested,
                    price=10000.0 + venue_index * 10 + time_index,
                    sequence=sequence,
                )

        features = build_source_time_v4_features(
            connection,
            target,
            decision_at=decision,
        )
        assert features.predictors["seconds_elapsed"] == 240
        assert features.predictors["seconds_remaining"] == 60
        assert all(name in features.predictors for name in V4_PREDICTOR_NAMES)
        assert features.predictors["bybit_linear_funding_rate"] == 0.0001
        assert features.predictors["bybit_linear_open_interest"] == 12345.0
        assert features.predictors["regime_bull"] in (0.0, 1.0)
        assert len(features.source_evidence) >= 24
        assert all(
            evidence.received_at <= evidence.requested_at
            for evidence in features.source_evidence.values()
        )
        assert all(
            evidence.source_age_seconds <= 2.0
            for evidence in features.source_evidence.values()
        )
        assert features.core_source_ready is True
        assert features.core_source_ineligible_reasons() == ()
    finally:
        connection.close()
        engine.dispose()


def test_source_time_reader_prefers_closest_provider_timestamp() -> None:
    engine, connection = _connection()
    requested = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    try:
        _insert_event(
            connection,
            source="coinbase",
            stream="spot",
            instrument="BTC-USD",
            requested_at=requested,
            price=100.0,
            sequence=101,
            source_age_seconds=0.40,
            received_lag_seconds=0.01,
        )
        _insert_event(
            connection,
            source="coinbase",
            stream="spot",
            instrument="BTC-USD",
            requested_at=requested,
            price=101.0,
            sequence=102,
            source_age_seconds=0.05,
            received_lag_seconds=0.04,
        )

        observation, evidence = V4SourceTimeReader().latest_price(
            connection,
            source="coinbase",
            stream="spot",
            instrument="BTC-USD",
            requested_at=requested,
        )

        assert observation is not None
        assert float(observation.price) == 101.0
        assert len(evidence) == 1
        assert evidence[0].source_age_seconds == 0.05
    finally:
        connection.close()
        engine.dispose()


def test_core_source_readiness_requires_market_start_and_current_all_venues() -> None:
    engine, connection = _connection()
    start = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    decision = start + timedelta(seconds=240)
    target = V4FeatureTarget(
        condition_id="condition-v4-source-ready",
        slug="btc-updown-5m-source-ready",
        horizon_seconds=300,
        market_start_at=start,
        market_end_at=start + timedelta(seconds=300),
    )
    requested_times = sorted(
        {
            start,
            decision - timedelta(seconds=120),
            decision - timedelta(seconds=60),
            decision - timedelta(seconds=30),
            decision,
            decision - timedelta(seconds=300),
            decision - timedelta(seconds=900),
            decision - timedelta(seconds=3600),
        }
    )
    sequence = 200
    try:
        for source, stream, instrument in (
            ("coinbase", "spot", "BTC-USD"),
            ("bybit", "spot", "BTCUSDT"),
            ("bybit", "linear", "BTCUSDT"),
        ):
            for requested in requested_times:
                if stream == "linear" and requested == decision:
                    continue
                sequence += 1
                _insert_event(
                    connection,
                    source=source,
                    stream=stream,
                    instrument=instrument,
                    requested_at=requested,
                    price=20000.0 + sequence,
                    sequence=sequence,
                )

        features = build_source_time_v4_features(
            connection,
            target,
            decision_at=decision,
        )

        assert features.core_source_ready is False
        assert features.core_source_ineligible_reasons() == (
            "bybit_linear_current_missing",
        )
        assert set(features.core_source_ineligible_reasons()).issubset(
            V4_CORE_SOURCE_REQUIRED_FLAGS
        )
        assert features.predictors["bybit_linear_return_60m"] is None
    finally:
        connection.close()
        engine.dispose()
