"""Offline collector-to-feature contract tests for frozen Bybit-linear V4.

Use only synthetic messages and an in-memory SQLite database. These tests
do not measure historical feeds, latency, visibility, or trade performance.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, insert, select

from bp_engine.collectors.bybit_ws import parse_bybit_message
from bp_engine.recorder.service import (
    _BufferedEventSink,
    _is_v4_priority_anchor_event,
    _RoutedBufferedEventSink,
)
from bp_engine.recorder.writer import BatchWriter, EventBuffer
from bp_engine.storage.recorder import RecorderRepository
from bp_engine.storage.schema import metadata, raw_market_events
from bp_engine.v4_paper.source_time_features import V4SourceTimeReader


def _millis(when: datetime) -> int:
    return int(when.timestamp() * 1000)


def _ticker(
    decision_at: datetime,
    *,
    received_lag_seconds: float = 0.1,
    data: dict[str, str],
    source_at: datetime | None = None,
):
    source = source_at or decision_at - timedelta(milliseconds=200)
    message = {
        "topic": "tickers.BTCUSDT",
        "type": "delta",
        "ts": _millis(source),
        "data": {"symbol": "BTCUSDT", **data},
    }
    return parse_bybit_message(
        message,
        venue="linear",
        received_at=decision_at - timedelta(seconds=received_lag_seconds),
    )[0]


def _trade(
    decision_at: datetime,
    *,
    received_lag_seconds: float = 0.1,
    price: str = "30002",
):
    source = decision_at - timedelta(milliseconds=200)
    message = {
        "topic": "publicTrade.BTCUSDT",
        "type": "snapshot",
        "ts": _millis(source),
        "data": [{"s": "BTCUSDT", "T": _millis(source), "p": price}],
    }
    return parse_bybit_message(
        message,
        venue="linear",
        received_at=decision_at - timedelta(seconds=received_lag_seconds),
    )[0]


def _save(connection, event) -> None:
    connection.execute(
        insert(raw_market_events).values(**event.model_dump())
    )


def _linear_price(connection, decision_at: datetime):
    return V4SourceTimeReader().latest_price(
        connection,
        source="bybit",
        stream="linear",
        instrument="BTCUSDT",
        requested_at=decision_at,
    )


def test_price_less_linear_ticker_routes_priority_but_is_not_v4_price() -> None:
    requested = datetime(2026, 10, 7, 20, 30, tzinfo=UTC)
    delta = _ticker(
        requested, data={"markPrice": "30001", "bid1Price": "30000"}
    )
    assert delta.source == "bybit"
    assert delta.stream == "linear"
    assert delta.event_type == "ticker"
    assert _is_v4_priority_anchor_event(delta) is True

    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            _save(connection, delta)
            observation, evidence = _linear_price(connection, requested)
            assert observation is None
            assert evidence == ()
    finally:
        engine.dispose()


def test_linear_trade_routes_bulk_but_can_satisfy_frozen_price_anchor() -> None:
    requested = datetime(2026, 10, 7, 20, 30, tzinfo=UTC)
    delta = _ticker(
        requested, data={"markPrice": "30001", "bid1Price": "30000"}
    )
    trade = _trade(requested, price="30002")
    assert _is_v4_priority_anchor_event(delta) is True
    assert _is_v4_priority_anchor_event(trade) is False
    assert trade.event_type == "trade"

    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            _save(connection, delta)
            _save(connection, trade)
            observation, evidence = _linear_price(connection, requested)
            assert observation is not None
            assert float(observation.price) == 30002.0
            assert len(evidence) == 1
            assert evidence[0].event_type == "trade"
            assert evidence[0].received_at <= requested
    finally:
        engine.dispose()


def test_eligible_linear_ticker_is_selected_despite_price_less_delta() -> None:
    requested = datetime(2026, 10, 7, 20, 30, tzinfo=UTC)
    snapshot = _ticker(
        requested,
        data={"lastPrice": "30003", "markPrice": "30001"},
        source_at=requested - timedelta(milliseconds=400),
    )
    later_delta = _ticker(
        requested,
        data={"markPrice": "30004"},
        source_at=requested - timedelta(milliseconds=150),
    )
    assert _is_v4_priority_anchor_event(snapshot)
    assert _is_v4_priority_anchor_event(later_delta)

    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            _save(connection, snapshot)
            _save(connection, later_delta)
            observation, evidence = _linear_price(connection, requested)
            assert observation is not None
            assert float(observation.price) == 30003.0
            assert len(evidence) == 1
            assert evidence[0].row_id == observation.row_id
    finally:
        engine.dispose()


def test_received_after_cutoff_or_missing_server_time_rejects_price() -> None:
    requested = datetime(2026, 10, 7, 20, 30, tzinfo=UTC)
    late_trade = _trade(
        requested,
        received_lag_seconds=-0.1,
    )
    message_without_ts = {
        "topic": "tickers.BTCUSDT",
        "type": "snapshot",
        "data": {"symbol": "BTCUSDT", "lastPrice": "30005"},
    }
    missing_source_time = parse_bybit_message(
        message_without_ts,
        venue="linear",
        received_at=requested - timedelta(milliseconds=100),
    )[0]
    assert missing_source_time.source_timestamp is None

    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            _save(connection, late_trade)
            _save(connection, missing_source_time)
            observation, evidence = _linear_price(connection, requested)
            assert observation is None
            assert evidence == ()
    finally:
        engine.dispose()



def _bulk_writer_storage_observation(event, requested: datetime):
    """Use real recorder routing, buffering, writer and SQLite repository."""

    async def execute():
        engine = create_engine("sqlite+pysqlite:///:memory:")
        metadata.create_all(engine)
        priority = EventBuffer(maxsize=4)
        bulk = EventBuffer(maxsize=4)
        incidents = []
        routed = _RoutedBufferedEventSink(
            priority_sink=_BufferedEventSink(priority, incidents.append),
            bulk_sink=_BufferedEventSink(bulk, incidents.append),
            incident_sink=incidents.append,
        )
        writes = []

        async def persist(events):
            with engine.begin() as connection:
                writes.append(RecorderRepository().insert_events(connection, events))

        try:
            await routed(event)
            assert priority.empty()
            assert bulk.qsize() == 1
            assert incidents == []

            # A pre-stopped writer drains the queue deterministically;
            # no live feed, background services, clock waits or network.
            stop = asyncio.Event()
            stop.set()
            writer = BatchWriter(
                buffer=bulk,
                sink=persist,
                batch_size=1,
                flush_interval_seconds=1,
            )
            await asyncio.wait_for(writer.run(stop), timeout=3)
            assert writes == [1]
            assert bulk.empty()

            with engine.connect() as connection:
                persisted = [
                    dict(row)
                    for row in connection.execute(
                        select(raw_market_events)
                        .where(raw_market_events.c.source == "bybit")
                        .where(raw_market_events.c.stream == "linear")
                        .where(raw_market_events.c.instrument == "BTCUSDT")
                        .where(raw_market_events.c.event_type == "trade")
                    ).mappings()
                ]
                stage_a_candidates = [
                    dict(row)
                    for row in connection.execute(
                        select(raw_market_events)
                        .where(raw_market_events.c.source == "bybit")
                        .where(raw_market_events.c.stream == "linear")
                        .where(raw_market_events.c.instrument == "BTCUSDT")
                        .where(raw_market_events.c.event_type == "trade")
                        .where(raw_market_events.c.source_timestamp.is_not(None))
                        .where(raw_market_events.c.received_at <= requested)
                        .where(
                            raw_market_events.c.received_at
                            >= requested - timedelta(seconds=12)
                        )
                    ).mappings()
                ]
                observation, evidence = _linear_price(connection, requested)
            return persisted, stage_a_candidates, observation, evidence
        finally:
            engine.dispose()

    return asyncio.run(execute())


def test_valid_linear_trade_survives_bulk_writer_and_is_queryable() -> None:
    """A source-correct trade survives all existing offline pipeline stages."""
    requested = datetime(2026, 10, 12, 21, 8, tzinfo=UTC)
    trade = _trade(requested, price="30107")
    assert trade.source_timestamp is not None
    assert not _is_v4_priority_anchor_event(trade)

    persisted, stage_a_candidates, observation, evidence = (
        _bulk_writer_storage_observation(trade, requested)
    )
    assert len(persisted) == 1
    assert persisted[0]["event_type"] == "trade"
    assert persisted[0]["payload"]["data"][-1]["p"] == "30107"
    assert persisted[0]["source_timestamp"] is not None
    assert len(stage_a_candidates) == 1
    assert observation is not None
    assert float(observation.price) == 30107.0
    assert len(evidence) == 1
    assert evidence[0].event_type == "trade"


def test_trade_without_envelope_timestamp_is_stored_but_v4_invisible() -> None:
    """A hypothetical payload with only data[].T has no V4 source timestamp."""
    requested = datetime(2026, 10, 12, 21, 8, tzinfo=UTC)
    source = requested - timedelta(milliseconds=200)
    message = {
        "topic": "publicTrade.BTCUSDT",
        "type": "snapshot",
        # Deliberately omit the top-level Bybit 'ts' field.
        "data": [{"s": "BTCUSDT", "T": _millis(source), "p": "30108"}],
    }
    trade = parse_bybit_message(
        message, venue="linear", received_at=requested - timedelta(milliseconds=100)
    )[0]
    assert trade.event_type == "trade"
    assert trade.source_timestamp is None
    assert trade.payload["data"][0]["T"] == _millis(source)
    assert not _is_v4_priority_anchor_event(trade)

    persisted, stage_a_candidates, observation, evidence = (
        _bulk_writer_storage_observation(trade, requested)
    )
    assert len(persisted) == 1
    assert persisted[0]["payload"]["data"][-1]["p"] == "30108"
    assert persisted[0]["source_timestamp"] is None
    assert stage_a_candidates == []
    assert observation is None
    assert evidence == ()
