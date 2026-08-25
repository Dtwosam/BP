from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, insert

from bp_engine.storage.schema import (
    btc_candles,
    market_state_1s,
    metadata,
    polymarket_price_history,
)


def _sources():
    return importlib.import_module("bp_engine.features.sources")


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    return engine


def test_latest_polymarket_price_includes_exact_feature_time_and_excludes_future() -> None:
    sources = _sources()
    engine = _engine()
    feature_at = datetime(2026, 8, 25, 10, 1, tzinfo=UTC)

    with engine.begin() as connection:
        connection.execute(
            insert(polymarket_price_history),
            [
                {
                    "source": "polymarket_clob_prices_history",
                    "condition_id": "condition-1",
                    "asset_id": "up-token",
                    "outcome": "Up",
                    "observed_at": feature_at - timedelta(seconds=1),
                    "price": Decimal("0.58"),
                    "fidelity_minutes": 1,
                },
                {
                    "source": "polymarket_clob_prices_history",
                    "condition_id": "condition-1",
                    "asset_id": "up-token",
                    "outcome": "Up",
                    "observed_at": feature_at,
                    "price": Decimal("0.60"),
                    "fidelity_minutes": 1,
                },
                {
                    "source": "polymarket_clob_prices_history",
                    "condition_id": "condition-1",
                    "asset_id": "up-token",
                    "outcome": "Up",
                    "observed_at": feature_at + timedelta(seconds=1),
                    "price": Decimal("0.99"),
                    "fidelity_minutes": 1,
                },
            ],
        )
        observation = sources.FeatureSourceReader().latest_polymarket_price(
            connection,
            condition_id="condition-1",
            outcome="Up",
            feature_at=feature_at,
        )

    assert observation is not None
    assert observation.price == Decimal("0.60")
    assert observation.observed_at == feature_at
    assert observation.effective_at == feature_at


def test_closed_candles_require_full_interval_to_have_elapsed() -> None:
    sources = _sources()
    engine = _engine()
    bucket_at = datetime(2026, 8, 25, 10, 0, tzinfo=UTC)

    with engine.begin() as connection:
        connection.execute(
            insert(btc_candles).values(
                source="coinbase",
                market_type="spot",
                symbol="BTC-USD",
                interval_seconds=60,
                bucket_at=bucket_at,
                open=Decimal("64000"),
                high=Decimal("64100"),
                low=Decimal("63900"),
                close=Decimal("64050"),
                volume=Decimal("10"),
                turnover=None,
                raw_payload={"fixture": True},
            )
        )
        before_close = sources.FeatureSourceReader().closed_candles(
            connection,
            source="coinbase",
            market_type="spot",
            symbol="BTC-USD",
            interval_seconds=60,
            feature_at=bucket_at + timedelta(seconds=60, microseconds=-1),
            limit=10,
        )
        at_close = sources.FeatureSourceReader().closed_candles(
            connection,
            source="coinbase",
            market_type="spot",
            symbol="BTC-USD",
            interval_seconds=60,
            feature_at=bucket_at + timedelta(seconds=60),
            limit=10,
        )

    assert before_close == ()
    assert len(at_close) == 1
    assert at_close[0].bucket_at == bucket_at
    assert at_close[0].effective_at == bucket_at + timedelta(seconds=60)


def test_latest_state_rejects_candidate_with_future_last_event() -> None:
    sources = _sources()
    engine = _engine()
    feature_at = datetime(2026, 8, 25, 10, 2, tzinfo=UTC)

    with engine.begin() as connection:
        connection.execute(
            insert(market_state_1s).values(
                bucket_at=feature_at,
                state_key="coinbase/ticker/BTC-USD",
                source="coinbase",
                stream="ticker",
                instrument="BTC-USD",
                market_id=None,
                asset_id=None,
                last_event_at=feature_at + timedelta(microseconds=1),
                state={"last_price": "64000"},
            )
        )
        with pytest.raises(sources.FeatureLeakageError, match="last_event_at"):
            sources.FeatureSourceReader().latest_state(
                connection,
                source="coinbase",
                stream="ticker",
                instrument="BTC-USD",
                feature_at=feature_at,
            )


def test_latest_state_returns_stale_observation_with_fresh_false() -> None:
    sources = _sources()
    engine = _engine()
    feature_at = datetime(2026, 8, 25, 10, 2, tzinfo=UTC)
    observed_at = feature_at - timedelta(seconds=11)

    with engine.begin() as connection:
        connection.execute(
            insert(market_state_1s).values(
                bucket_at=observed_at,
                state_key="coinbase/ticker/BTC-USD",
                source="coinbase",
                stream="ticker",
                instrument="BTC-USD",
                market_id=None,
                asset_id=None,
                last_event_at=observed_at,
                state={"last_price": "64000"},
            )
        )
        observation = sources.FeatureSourceReader().latest_state(
            connection,
            source="coinbase",
            stream="ticker",
            instrument="BTC-USD",
            feature_at=feature_at,
        )

    assert observation is not None
    assert observation.fresh is False
    assert observation.age_seconds == 11.0
    assert observation.effective_at == observed_at


def test_latest_state_filters_asset_id_without_crossing_tokens() -> None:
    sources = _sources()
    engine = _engine()
    feature_at = datetime(2026, 8, 25, 10, 2, tzinfo=UTC)

    with engine.begin() as connection:
        for asset_id, price in (("up-token", "0.62"), ("down-token", "0.38")):
            connection.execute(
                insert(market_state_1s).values(
                    bucket_at=feature_at,
                    state_key=f"polymarket/market/condition-1/{asset_id}",
                    source="polymarket",
                    stream="market",
                    instrument="condition-1",
                    market_id="condition-1",
                    asset_id=asset_id,
                    last_event_at=feature_at,
                    state={"last_price": price},
                )
            )
        observation = sources.FeatureSourceReader().latest_state(
            connection,
            source="polymarket",
            stream="market",
            instrument="condition-1",
            asset_id="up-token",
            feature_at=feature_at,
        )

    assert observation is not None
    assert observation.asset_id == "up-token"
    assert observation.state["last_price"] == "0.62"
