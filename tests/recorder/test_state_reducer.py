import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from bp_engine.collectors.bybit_ws import parse_bybit_message
from bp_engine.collectors.coinbase_ws import parse_coinbase_message
from bp_engine.collectors.polymarket_ws import parse_polymarket_message
from bp_engine.recorder.models import RawEvent
from bp_engine.recorder.state import MarketStateReducer

FIXTURES = Path(__file__).parents[1] / "fixtures"


def load(folder: str, name: str) -> dict[str, object]:
    return json.loads((FIXTURES / folder / name).read_text())


def test_polymarket_book_reduces_best_quotes_and_displayed_depth() -> None:
    received_at = datetime(2026, 8, 23, 20, 12, 57, 125_000, tzinfo=UTC)
    event = parse_polymarket_message(
        load("polymarket_ws", "book.json"), received_at=received_at
    )[0]
    reducer = MarketStateReducer()

    reducer.observe(event)
    snapshots = reducer.snapshots(datetime(2026, 8, 23, 20, 12, 58, 900_000, tzinfo=UTC))

    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.bucket_at == datetime(2026, 8, 23, 20, 12, 58, tzinfo=UTC)
    assert snapshot.last_event_at == received_at
    assert snapshot.asset_id == event.asset_id
    assert snapshot.state["best_bid"] == "0.49"
    assert snapshot.state["best_ask"] == "0.52"
    assert snapshot.state["bid_depth"] == "50"
    assert snapshot.state["ask_depth"] == "85"


def test_polymarket_price_change_tracks_each_asset_without_calling_it_a_trade() -> None:
    received_at = datetime(2026, 8, 23, 20, 13, tzinfo=UTC)
    event = parse_polymarket_message(
        load("polymarket_ws", "price_change.json"), received_at=received_at
    )[0]
    reducer = MarketStateReducer()

    reducer.observe(event)
    snapshot = reducer.snapshots(received_at)[0]

    assert snapshot.asset_id == event.payload["price_changes"][0]["asset_id"]
    assert snapshot.state["best_bid"] == "0.5"
    assert snapshot.state["best_ask"] == "1"
    assert snapshot.state["last_change_price"] == "0.5"
    assert snapshot.state["last_change_size"] == "200"
    assert snapshot.state["last_change_side"] == "BUY"
    assert "last_price" not in snapshot.state


def test_polymarket_last_trade_preserves_dedicated_provenance() -> None:
    received_at = datetime(2026, 8, 23, 20, 13, 30, 250_000, tzinfo=UTC)
    event = parse_polymarket_message(
        load("polymarket_ws", "last_trade_price.json"), received_at=received_at
    )[0]
    reducer = MarketStateReducer()

    reducer.observe(event)
    snapshot = reducer.snapshots(received_at)[0]

    assert event.source_timestamp is not None
    assert snapshot.state["last_trade_price"] == event.payload["price"]
    assert snapshot.state["last_price"] == event.payload["price"]
    assert snapshot.state["last_trade_size"] == event.payload["size"]
    assert snapshot.state["last_trade_side"] == event.payload["side"]
    assert snapshot.state["last_trade_source_at"] == event.source_timestamp.isoformat().replace(
        "+00:00", "Z"
    )
    assert snapshot.state["last_trade_received_at"] == event.received_at.isoformat().replace(
        "+00:00", "Z"
    )
    assert snapshot.state["last_trade_event_dedupe_key"] == event.dedupe_key


def test_polymarket_book_activity_does_not_refresh_last_trade_provenance() -> None:
    received_at = datetime(2026, 8, 23, 20, 13, 40, tzinfo=UTC)
    trade = parse_polymarket_message(
        load("polymarket_ws", "last_trade_price.json"), received_at=received_at
    )[0]
    assert trade.asset_id is not None
    later_received_at = received_at.replace(microsecond=500_000)
    price_change = RawEvent.build(
        source="polymarket",
        stream="market",
        instrument=trade.instrument,
        event_type="price_change",
        source_timestamp=later_received_at,
        received_at=later_received_at,
        market_id=trade.market_id,
        payload={
            "event_type": "price_change",
            "market": trade.market_id,
            "price_changes": [
                {
                    "asset_id": trade.asset_id,
                    "price": "0.50",
                    "size": "12",
                    "side": "BUY",
                    "best_bid": "0.49",
                    "best_ask": "0.51",
                }
            ],
        },
    )
    reducer = MarketStateReducer()

    reducer.observe(trade)
    before = reducer.snapshots(received_at)[0]
    reducer.observe(price_change)
    after = reducer.snapshots(later_received_at)[0]

    keys = (
        "last_trade_price",
        "last_trade_size",
        "last_trade_side",
        "last_trade_source_at",
        "last_trade_received_at",
        "last_trade_event_dedupe_key",
    )
    assert after.last_event_at == later_received_at
    assert {key: after.state[key] for key in keys} == {
        key: before.state[key] for key in keys
    }
    assert after.state["last_change_price"] == "0.50"


def test_polymarket_last_trade_without_source_timestamp_is_not_timestamped_evidence() -> None:
    received_at = datetime(2026, 8, 23, 20, 13, 50, tzinfo=UTC)
    event = RawEvent.build(
        source="polymarket",
        stream="market",
        instrument="condition-without-source-time",
        event_type="last_trade_price",
        source_timestamp=None,
        received_at=received_at,
        market_id="condition-without-source-time",
        asset_id="token-without-source-time",
        payload={
            "event_type": "last_trade_price",
            "asset_id": "token-without-source-time",
            "market": "condition-without-source-time",
            "price": "0.42",
            "size": "3",
            "side": "SELL",
        },
    )
    reducer = MarketStateReducer()

    reducer.observe(event)
    snapshot = reducer.snapshots(received_at)[0]

    assert snapshot.state["last_trade_price"] == "0.42"
    assert snapshot.state["last_trade_source_at"] is None
    assert snapshot.state["last_trade_received_at"] == received_at.isoformat().replace(
        "+00:00", "Z"
    )
    assert snapshot.state["last_trade_event_dedupe_key"] == event.dedupe_key


def test_bybit_zero_size_delta_removes_old_best_bid() -> None:
    received_at = datetime(2026, 8, 23, 20, 14, tzinfo=UTC)
    snapshot_payload = deepcopy(load("bybit", "orderbook_snapshot.json"))
    snapshot_payload["data"]["b"] = [
        ["30247.20", "30.028"],
        ["30246.00", "2"],
    ]
    delete_best = deepcopy(load("bybit", "orderbook_delta.json"))
    delete_best["data"]["b"] = [["30247.20", "0"]]
    reducer = MarketStateReducer()

    reducer.observe(
        parse_bybit_message(snapshot_payload, venue="spot", received_at=received_at)[0]
    )
    reducer.observe(
        parse_bybit_message(
            delete_best,
            venue="spot",
            received_at=received_at.replace(microsecond=500_000),
        )[0]
    )
    state = reducer.snapshots(received_at.replace(second=1))[0].state

    assert state["best_bid"] == "30246.00"
    assert state["best_ask"] == "30248.70"
    assert state["bid_depth"] == "2"
    assert state["ask_depth"] == "2.142"


def test_bybit_linear_state_keeps_derivatives_fields_and_latest_trade() -> None:
    received_at = datetime(2026, 8, 23, 20, 15, tzinfo=UTC)
    reducer = MarketStateReducer()
    ticker = parse_bybit_message(
        load("bybit", "linear_ticker.json"),
        venue="linear",
        received_at=received_at,
    )[0]
    trade = parse_bybit_message(
        load("bybit", "public_trade.json"),
        venue="linear",
        received_at=received_at.replace(microsecond=250_000),
    )[0]

    reducer.observe(ticker)
    reducer.observe(trade)
    snapshot = reducer.snapshots(received_at.replace(second=1))[0]

    assert snapshot.state["mark_price"] == "66666.60"
    assert snapshot.state["index_price"] == "115418.19"
    assert snapshot.state["funding_rate"] == "-0.005"
    assert snapshot.state["open_interest"] == "492373.72"
    assert snapshot.state["last_price"] == "16578.50"
    assert snapshot.state["last_trade_size"] == "0.001"
    assert snapshot.state["last_trade_side"] == "Buy"


def test_coinbase_ticker_and_trade_share_one_compact_state() -> None:
    received_at = datetime(2026, 8, 23, 20, 16, tzinfo=UTC)
    reducer = MarketStateReducer()
    ticker = parse_coinbase_message(
        load("coinbase", "ticker.json"), received_at=received_at
    )[0]
    trade = parse_coinbase_message(
        load("coinbase", "market_trades.json"),
        received_at=received_at.replace(microsecond=400_000),
    )[0]

    reducer.observe(ticker)
    reducer.observe(trade)
    snapshots = reducer.snapshots(received_at.replace(second=1))

    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.state_key == "coinbase/spot/BTC-USD"
    assert snapshot.state["best_bid"] == "72690.01"
    assert snapshot.state["best_ask"] == "72690.02"
    assert snapshot.state["last_price"] == "72001.00"
    assert snapshot.state["last_trade_size"] == "0.01"
    assert snapshot.state["last_trade_side"] == "BUY"
    assert snapshot.last_event_at == trade.received_at
