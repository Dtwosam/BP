from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, insert

from bp_engine.storage.schema import metadata, raw_market_events


def _flow():
    return importlib.import_module("bp_engine.features.trade_flow")


def _raw_event(
    *,
    source: str,
    stream: str,
    event_type: str,
    payload: dict[str, object],
    received_at: datetime,
    dedupe_key: str,
    instrument: str = "BTC-USD",
) -> dict[str, object]:
    return {
        "source": source,
        "stream": stream,
        "instrument": instrument,
        "event_type": event_type,
        "source_timestamp": received_at,
        "received_at": received_at,
        "sequence": None,
        "market_id": None,
        "asset_id": None,
        "payload": payload,
        "dedupe_key": dedupe_key,
    }


def test_polymarket_native_side_trade_flow() -> None:
    flow = _flow()
    when = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    events = [
        _raw_event(
            source="polymarket",
            stream="market",
            event_type="last_trade_price",
            payload={"side": "BUY", "size": "3.5", "price": "0.62"},
            received_at=when,
            dedupe_key="pm-buy",
            instrument="condition-1",
        ),
        _raw_event(
            source="polymarket",
            stream="market",
            event_type="last_trade_price",
            payload={"side": "SELL", "size": "1.25", "price": "0.61"},
            received_at=when,
            dedupe_key="pm-sell",
            instrument="condition-1",
        ),
    ]

    result = flow.parse_trade_flow(events, source="polymarket", stream="market")

    assert result is not None
    assert result.buy_volume == Decimal("3.5")
    assert result.sell_volume == Decimal("1.25")
    assert result.signed_volume == Decimal("2.25")
    assert result.trade_count == 2
    assert [item["dedupe_key"] for item in result.observations] == ["pm-buy", "pm-sell"]


def test_coinbase_market_trades_preserve_reported_side() -> None:
    flow = _flow()
    when = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    events = [
        _raw_event(
            source="coinbase",
            stream="spot",
            event_type="market_trades_update",
            payload={
                "events": [
                    {
                        "trades": [
                            {"side": "BUY", "size": "2", "price": "64000"},
                            {"side": "SELL", "size": "0.5", "price": "64010"},
                        ]
                    }
                ]
            },
            received_at=when,
            dedupe_key="cb-trades",
        )
    ]

    result = flow.parse_trade_flow(events, source="coinbase", stream="spot")

    assert result is not None
    assert result.buy_volume == Decimal("2")
    assert result.sell_volume == Decimal("0.5")
    assert result.signed_volume == Decimal("1.5")
    assert result.trade_count == 2
    assert len(result.observations) == 2


def test_bybit_trade_list_preserves_reported_side() -> None:
    flow = _flow()
    when = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    events = [
        _raw_event(
            source="bybit",
            stream="linear",
            event_type="trade",
            payload={
                "data": [
                    {"S": "Buy", "v": "4", "p": "64000"},
                    {"S": "Sell", "v": "1.5", "p": "64001"},
                ]
            },
            received_at=when,
            dedupe_key="by-trades",
            instrument="BTCUSDT",
        )
    ]

    result = flow.parse_trade_flow(events, source="bybit", stream="linear")

    assert result is not None
    assert result.buy_volume == Decimal("4")
    assert result.sell_volume == Decimal("1.5")
    assert result.signed_volume == Decimal("2.5")
    assert result.trade_count == 2


def test_trade_with_size_but_missing_or_unknown_side_fails_closed() -> None:
    flow = _flow()
    when = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    events = [
        _raw_event(
            source="polymarket",
            stream="market",
            event_type="last_trade_price",
            payload={"size": "1", "price": "0.62"},
            received_at=when,
            dedupe_key="bad-side",
            instrument="condition-1",
        )
    ]

    with pytest.raises(flow.TradeFlowError, match="side"):
        flow.parse_trade_flow(events, source="polymarket", stream="market")


def test_no_feed_events_is_missing_but_feed_events_with_no_trades_is_zero_flow() -> None:
    flow = _flow()
    when = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)

    assert flow.parse_trade_flow([], source="coinbase", stream="spot") is None

    heartbeat_like = [
        _raw_event(
            source="coinbase",
            stream="spot",
            event_type="ticker_update",
            payload={"events": [{"tickers": [{"price": "64000"}]}]},
            received_at=when,
            dedupe_key="coverage",
        )
    ]
    result = flow.parse_trade_flow(heartbeat_like, source="coinbase", stream="spot")

    assert result is not None
    assert result.buy_volume == 0
    assert result.sell_volume == 0
    assert result.signed_volume == 0
    assert result.trade_count == 0
    assert result.observations == ()


def test_load_trade_flow_uses_open_left_closed_right_trailing_window() -> None:
    flow = _flow()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    feature_at = datetime(2026, 8, 25, 12, 1, tzinfo=UTC)
    left = feature_at - timedelta(seconds=60)

    rows = [
        _raw_event(
            source="coinbase",
            stream="spot",
            event_type="market_trades_update",
            payload={"events": [{"trades": [{"side": "BUY", "size": "100"}]}]},
            received_at=left,
            dedupe_key="left-excluded",
        ),
        _raw_event(
            source="coinbase",
            stream="spot",
            event_type="market_trades_update",
            payload={"events": [{"trades": [{"side": "BUY", "size": "2"}]}]},
            received_at=left + timedelta(microseconds=1),
            dedupe_key="inside",
        ),
        _raw_event(
            source="coinbase",
            stream="spot",
            event_type="market_trades_update",
            payload={"events": [{"trades": [{"side": "SELL", "size": "0.5"}]}]},
            received_at=feature_at,
            dedupe_key="right-included",
        ),
        _raw_event(
            source="coinbase",
            stream="spot",
            event_type="market_trades_update",
            payload={"events": [{"trades": [{"side": "BUY", "size": "200"}]}]},
            received_at=feature_at + timedelta(microseconds=1),
            dedupe_key="future-excluded",
        ),
    ]

    with engine.begin() as connection:
        connection.execute(insert(raw_market_events), rows)
        result = flow.load_trade_flow(
            connection,
            source="coinbase",
            stream="spot",
            instrument="BTC-USD",
            feature_at=feature_at,
        )

    assert result is not None
    assert result.buy_volume == Decimal("2")
    assert result.sell_volume == Decimal("0.5")
    assert result.trade_count == 2


def test_load_trade_flow_marks_known_exclusion_without_querying_as_zero() -> None:
    flow = _flow()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    feature_at = datetime(2026, 8, 22, 20, 0, 30, tzinfo=UTC)

    with engine.begin() as connection:
        result = flow.load_trade_flow(
            connection,
            source="coinbase",
            stream="spot",
            instrument="BTC-USD",
            feature_at=feature_at,
        )

    assert result is None
