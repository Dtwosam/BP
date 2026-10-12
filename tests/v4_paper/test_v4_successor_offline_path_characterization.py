"""Synthetic characterization only: no host, market-data network, or live shadow.

These tests intentionally document the frozen runner's observed semantics;
they do not change the two-second acceptance policy or claim a runtime cause.
"""

from __future__ import annotations

import ast
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, insert

from bp_engine.features.v4_models import V4FeatureTarget
from bp_engine.storage.schema import metadata, raw_market_events
from bp_engine.v4_paper.source_time_features import (
    V4SourceTimeReader,
    build_source_time_v4_features,
    probe_core_source_time_v4_readiness,
)

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "run_v4_fresh_book_shadow.py"


def _target() -> V4FeatureTarget:
    start = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    return V4FeatureTarget(
        condition_id="synthetic-v4",
        slug="btc-updown-5m-synthetic",
        horizon_seconds=300,
        market_start_at=start,
        market_end_at=start + timedelta(seconds=300),
    )


def _insert_price(
    connection,
    *,
    source: str,
    stream: str,
    requested_at: datetime,
    sequence: int,
    event_type: str | None = None,
    payload: dict | None = None,
    source_age: float = 0.2,
    received_lag: float = 0.1,
) -> None:
    instrument = "BTC-USD" if source == "coinbase" else "BTCUSDT"
    kind = event_type or ("ticker_update" if source == "coinbase" else "ticker")
    if payload is None:
        payload = (
            {"events": [{"tickers": [{"price": "30000.5"}]}]}
            if source == "coinbase"
            else {"data": {"lastPrice": "30000.5"}}
        )
    connection.execute(
        insert(raw_market_events).values(
            source=source,
            stream=stream,
            instrument=instrument,
            event_type=kind,
            source_timestamp=requested_at - timedelta(seconds=source_age),
            received_at=requested_at - timedelta(seconds=received_lag),
            sequence=str(sequence),
            market_id=None,
            asset_id=None,
            payload=payload,
            dedupe_key=f"synthetic-{sequence}",
        )
    )


def test_frozen_runner_uses_batch_timestamp_in_each_initial_lag() -> None:
    """Inspect the actual runner AST; do not import or invoke main()."""
    tree = ast.parse(RUNNER.read_text(encoding="utf-8"))
    main = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    batch = next(node for node in ast.walk(main) if isinstance(node, ast.While))
    batch_now = next(
        node for node in batch.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "now" for t in node.targets)
    )
    market_loop = next(
        node for node in ast.walk(batch)
        if isinstance(node, ast.For) and ast.unparse(node.iter) == "rows"
    )
    initial = next(
        node for node in ast.walk(market_loop)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "initial_lag" for t in node.targets)
    )
    assert batch_now.lineno < market_loop.lineno < initial.lineno
    assert ast.unparse(initial.value) == "(now - decision_at).total_seconds()"
    assert not any(
        isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "now" for t in node.targets)
        for node in ast.walk(market_loop)
    )
    assert "feature_inference_lag_exceeded" in ast.unparse(market_loop)

    # A synthetic earlier market or quote wait delays the next market.
    # At 0.1s batch age it passes the initial test, even though its
    # processing starts at 2.6s, beyond the frozen 2s deadline.
    decision_at = datetime(2026, 10, 2, 12, 4, tzinfo=UTC)
    captured_now = decision_at + timedelta(milliseconds=100)
    actual_row_start = decision_at + timedelta(milliseconds=2600)
    recorded_at = decision_at + timedelta(milliseconds=2750)
    initial_lag = (captured_now - decision_at).total_seconds()
    assert initial_lag <= 2.0
    assert (actual_row_start - decision_at).total_seconds() > 2.0
    assert (recorded_at - decision_at).total_seconds() > 2.0
    # This exhibits a *possible* attribution error, not a measured
    # count among the 116 historical misses.


def test_shared_reader_reselects_six_required_anchors_offline(monkeypatch) -> None:
    """Exercise real readiness/features using synthetic SQLite events."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    target = _target()
    start = target.market_start_at
    decision = start + timedelta(seconds=240)
    requested_times = sorted({
        start,
        decision - timedelta(seconds=120),
        decision - timedelta(seconds=60),
        decision - timedelta(seconds=30),
        decision,
        decision - timedelta(seconds=300),
        decision - timedelta(seconds=900),
        decision - timedelta(seconds=3600),
    })
    reader = V4SourceTimeReader()
    seen_queries: list[tuple[str, str, str, datetime, bool]] = []
    original = reader._candidate_rows

    def traced(connection, *, source, stream, instrument, requested_at, ticker_only=False):
        seen_queries.append((source, stream, instrument, requested_at, ticker_only))
        return original(
            connection,
            source=source,
            stream=stream,
            instrument=instrument,
            requested_at=requested_at,
            ticker_only=ticker_only,
        )

    monkeypatch.setattr(reader, "_candidate_rows", traced)
    try:
        with engine.begin() as connection:
            seq = 0
            for source, stream in (("coinbase", "spot"), ("bybit", "spot"), ("bybit", "linear")):
                for requested in requested_times:
                    seq += 1
                    _insert_price(
                        connection,
                        source=source,
                        stream=stream,
                        requested_at=requested,
                        sequence=seq,
                    )
            readiness = probe_core_source_time_v4_readiness(
                connection, target, decision_at=decision, reader=reader
            )
            first_stage_keys = set(seen_queries)
            assert readiness.core_source_ready
            assert len(first_stage_keys) == 6
            assert len(seen_queries) == 6

            features = build_source_time_v4_features(
                connection, target, decision_at=decision, reader=reader
            )
            assert features.core_source_ready
            tally = Counter(seen_queries)
            assert len(seen_queries) == 31  # 6 + (3 venues * 8) + linear ticker state
            for key in first_stage_keys:
                assert tally[key] == 2
            assert tally[("bybit", "linear", "BTCUSDT", decision, True)] == 1
    finally:
        engine.dispose()
    # SQLite uses the unpartitioned fallback path; this proves duplicated
    # candidate selections in that mode, not live PostgreSQL timings.


def test_prefetch_discards_previous_core_cache_without_postgres() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    reader = V4SourceTimeReader()
    start = _target().market_start_at
    key = reader._candidate_cache_key(
        source="bybit",
        stream="linear",
        instrument="BTCUSDT",
        requested_at=start,
        ticker_only=False,
    )
    reader._prefetched_candidate_rows[key] = [{"sentinel": "prior stage"}]
    try:
        with engine.connect() as connection:
            reader.prefetch_candidate_rows(
                connection, (("bybit", "linear", "BTCUSDT", start, False),)
            )
        assert key not in reader._prefetched_candidate_rows
    finally:
        engine.dispose()


def test_bybit_linear_ticker_without_lastprice_is_missing_but_trade_can_qualify() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    requested = _target().market_start_at + timedelta(seconds=240)
    try:
        with engine.begin() as connection:
            _insert_price(
                connection, source="bybit", stream="linear",
                requested_at=requested, sequence=100,
                payload={"data": {"markPrice": "30001", "bid1Price": "30000"}},
            )
            reader = V4SourceTimeReader()
            missing, evidence = reader.latest_price(
                connection, source="bybit", stream="linear",
                instrument="BTCUSDT", requested_at=requested
            )
            assert missing is None and evidence == ()

            _insert_price(
                connection, source="bybit", stream="linear",
                requested_at=requested, sequence=101,
                event_type="trade", payload={"data": [{"p": "30003"}]},
            )
            price, evidence = reader.latest_price(
                connection, source="bybit", stream="linear",
                instrument="BTCUSDT", requested_at=requested
            )
            assert price is not None and float(price.price) == 30003.0
            assert len(evidence) == 1 and evidence[0].event_type == "trade"
    finally:
        engine.dispose()


def test_bybit_linear_old_or_after_cutoff_event_does_not_qualify() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    requested = _target().market_start_at + timedelta(seconds=240)
    try:
        with engine.begin() as connection:
            _insert_price(
                connection, source="bybit", stream="linear",
                requested_at=requested, sequence=201, source_age=3.0,
            )
            _insert_price(
                connection, source="bybit", stream="linear",
                requested_at=requested, sequence=202, received_lag=-0.1,
            )
            price, evidence = V4SourceTimeReader().latest_price(
                connection, source="bybit", stream="linear",
                instrument="BTCUSDT", requested_at=requested
            )
            assert price is None and evidence == ()
    finally:
        engine.dispose()
