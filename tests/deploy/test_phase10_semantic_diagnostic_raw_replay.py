from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine

from bp_engine.recorder.models import RawEvent
from bp_engine.recorder.state import MarketStateReducer
from bp_engine.storage.recorder import RecorderRepository
from bp_engine.storage.schema import metadata


def _diagnostic_module():
    path = Path("scripts/deploy/phase10_semantic_hash_diagnostic.py")
    spec = importlib.util.spec_from_file_location("phase10_semantic_hash_diagnostic", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _book_event(*, received_at: datetime, bid: str, ask: str) -> RawEvent:
    return RawEvent.build(
        source="polymarket",
        stream="market",
        instrument="condition",
        event_type="book",
        source_timestamp=received_at,
        received_at=received_at,
        market_id="market",
        asset_id="up",
        payload={
            "asset_id": "up",
            "bids": [{"price": bid, "size": "10"}],
            "asks": [{"price": ask, "size": "11"}],
        },
    )


def test_raw_replay_recovers_state_overwritten_after_prediction_cutoff() -> None:
    module = _diagnostic_module()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    scheduled = datetime(2026, 8, 27, 12, 4, tzinfo=UTC)
    original_event = _book_event(
        received_at=scheduled - timedelta(milliseconds=200),
        bid="0.58",
        ask="0.60",
    )
    late_event = _book_event(
        received_at=scheduled + timedelta(milliseconds=200),
        bid="0.57",
        ask="0.61",
    )
    repository = RecorderRepository()
    reducer = MarketStateReducer()

    with engine.begin() as connection:
        repository.insert_events(connection, (original_event, late_event))
        reducer.observe(original_event)
        repository.upsert_state_snapshots(connection, reducer.snapshots(scheduled))
        reducer.observe(late_event)
        repository.upsert_state_snapshots(connection, reducer.snapshots(scheduled))

        row = {
            "condition_id": "condition",
            "up_token_id": "up",
            "scheduled_at": scheduled,
            "up_book_cutoff_at": scheduled,
            "up_book_fresh": True,
        }
        current, status = module._state_for_prediction(connection, row, "up")
        replayed = module._replayed_state_candidates(connection, row, "up")

    assert current is None or current.effective_at != scheduled
    assert status == "source_missing" or status == "cutoff_mismatch"
    assert len(replayed) == 1
    assert replayed[0].effective_at == scheduled
    assert replayed[0].state["best_bid"] == "0.58"
    assert replayed[0].state["best_ask"] == "0.60"
