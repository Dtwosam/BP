from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine, insert

from bp_engine.features.models import FeatureTarget
from bp_engine.storage.schema import (
    btc_candles,
    market_state_1s,
    metadata,
    polymarket_price_history,
    raw_market_events,
)


def _service():
    return importlib.import_module("bp_engine.features.service")


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    return engine


def _target() -> FeatureTarget:
    start = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    return FeatureTarget(
        condition_id="condition-future-proof",
        slug="btc-updown-5m-future-proof",
        horizon_seconds=300,
        market_start_at=start,
        market_end_at=start + timedelta(minutes=5),
    )


def _candle(bucket_at: datetime, close: str) -> dict[str, object]:
    value = Decimal(close)
    return {
        "source": "coinbase",
        "market_type": "spot",
        "symbol": "BTC-USD",
        "interval_seconds": 60,
        "bucket_at": bucket_at,
        "open": value,
        "high": value,
        "low": value,
        "close": value,
        "volume": Decimal("1"),
        "turnover": None,
        "raw_payload": {"fixture": True},
    }


def _price(outcome: str, observed_at: datetime, price: str, asset: str) -> dict[str, object]:
    return {
        "source": "polymarket_clob_prices_history",
        "condition_id": "condition-future-proof",
        "asset_id": asset,
        "outcome": outcome,
        "observed_at": observed_at,
        "price": Decimal(price),
        "fidelity_minutes": 1,
    }


def test_future_data_perturbation_leaves_feature_at_t_byte_equivalent() -> None:
    service = _service()
    engine = _engine()
    target = _target()
    feature_at = target.market_start_at + timedelta(minutes=2)
    generated_at = feature_at + timedelta(hours=1)

    historical_candles = [
        _candle(target.market_start_at - timedelta(minutes=17 - index), str(63000 + index))
        for index in range(17)
    ]

    with engine.begin() as connection:
        connection.execute(insert(btc_candles), historical_candles)
        connection.execute(
            insert(polymarket_price_history),
            [
                _price("Up", feature_at, "0.61", "up-token"),
                _price("Down", feature_at, "0.38", "down-token"),
            ],
        )
        before = service.build_feature(
            connection,
            target,
            feature_at,
            generated_at=generated_at,
        )

        connection.execute(
            insert(polymarket_price_history),
            [
                _price("Up", feature_at + timedelta(microseconds=1), "0.99", "up-token"),
                _price("Down", feature_at + timedelta(microseconds=1), "0.01", "down-token"),
            ],
        )
        connection.execute(
            insert(btc_candles),
            [_candle(feature_at, "999999")],
        )
        connection.execute(
            insert(market_state_1s).values(
                bucket_at=feature_at + timedelta(microseconds=1),
                state_key="coinbase/spot/BTC-USD",
                source="coinbase",
                stream="spot",
                instrument="BTC-USD",
                market_id=None,
                asset_id=None,
                last_event_at=feature_at + timedelta(microseconds=1),
                state={"last_price": "999999"},
            )
        )
        connection.execute(
            insert(raw_market_events).values(
                source="coinbase",
                stream="spot",
                instrument="BTC-USD",
                event_type="market_trades_update",
                source_timestamp=feature_at + timedelta(microseconds=1),
                received_at=feature_at + timedelta(microseconds=1),
                sequence=None,
                market_id=None,
                asset_id=None,
                payload={"events": [{"trades": [{"side": "BUY", "size": "999"}]}]},
                dedupe_key="future-trade",
            )
        )
        after = service.build_feature(
            connection,
            target,
            feature_at,
            generated_at=generated_at + timedelta(hours=1),
        )

    assert before.features == after.features
    assert before.missing_flags == after.missing_flags
    assert before.source_cutoffs == after.source_cutoffs
    assert before.input_fingerprint == after.input_fingerprint
    assert before.feature_hash == after.feature_hash
