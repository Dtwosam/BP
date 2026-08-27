from __future__ import annotations

import importlib
import os
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, delete

from bp_engine.calibration.models import CalibrationFit, EdgeConfig
from bp_engine.features.calculators import book_state
from bp_engine.features.hashing import canonical_hash
from bp_engine.features.sources import FeatureSourceReader, StateObservation
from bp_engine.live_prediction.inputs import (
    LiveMarketInput,
    _book_descriptor,
    _book_input,
    _merge_book_predictors,
)
from bp_engine.live_prediction.models import LivePolicySpec
from bp_engine.live_prediction.predictor import build_live_prediction
from bp_engine.recorder.models import RawEvent
from bp_engine.recorder.state import MarketStateReducer
from bp_engine.storage import schema
from bp_engine.storage.recorder import RecorderRepository

DATABASE_URL = os.getenv("BP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BP_TEST_DATABASE_URL is required for PostgreSQL integration coverage",
)


def _policy() -> LivePolicySpec:
    return LivePolicySpec(
        source_calibration_run_id="phase9-source",
        source_calibration_semantic_sha256="1" * 64,
        source_backtest_run_id="phase8-source",
        source_backtest_semantic_sha256="2" * 64,
        source_training_run_id="phase7-source",
        source_training_semantic_sha256="3" * 64,
        calibration_version="platt-or-identity-v1",
        edge_policy_version="selected-ask-edge-v1",
        source_feature_version="core-v1",
        label_version="official-outcome-v1",
        horizon_seconds=300,
        selected_offset_seconds=240,
        calibration_fit=CalibrationFit(method="identity", intercept=None, coefficient=None),
        edge_config=EdgeConfig(
            fee_rate=0.07,
            slippage_buffer=0.01,
            min_edge_grid=(0.0, 0.02, 0.05),
            min_validation_trades=3,
            max_spread=None,
        ),
        edge_policy="trade_threshold",
        min_edge=0.02,
        training_prior=0.48,
        policy_sha256="4" * 64,
    )


def _book_event(
    *,
    asset_id: str,
    received_at: datetime,
    bid: str,
    ask: str,
) -> RawEvent:
    return RawEvent.build(
        source="polymarket",
        stream="market",
        instrument="condition-provenance-recovery",
        event_type="book",
        source_timestamp=received_at,
        received_at=received_at,
        market_id="market-provenance-recovery",
        asset_id=asset_id,
        payload={
            "asset_id": asset_id,
            "bids": [{"price": bid, "size": "10"}],
            "asks": [{"price": ask, "size": "11"}],
        },
    )


def _input_fingerprint(
    *,
    start: datetime,
    end: datetime,
    scheduled: datetime,
    probability: float,
    up_state: StateObservation,
    down_state: StateObservation,
) -> str:
    predictors = _merge_book_predictors(
        probability,
        book_state("pm_up", up_state),
        book_state("pm_down", down_state),
    )
    return canonical_hash(
        {
            "condition_id": "condition-provenance-recovery",
            "up_token_id": "up-provenance-recovery",
            "down_token_id": "down-provenance-recovery",
            "market_start_at": start,
            "market_end_at": end,
            "scheduled_at": scheduled,
            "downloaded_at": scheduled + timedelta(seconds=1),
            "price_source": "polymarket_clob",
            "price_dataset": "prices_history",
            "price_request_params": {
                "market": "up-provenance-recovery",
                "startTs": str(int(start.timestamp())),
                "endTs": str(int(scheduled.timestamp())),
                "fidelity": "1",
            },
            "price_response_sha256": "5" * 64,
            "market_probability_observed": True,
            "market_probability": probability,
            "market_probability_observed_at": scheduled,
            "up_book": _book_descriptor(_book_input(up_state)),
            "down_book": _book_descriptor(_book_input(down_state)),
            "predictors": predictors,
        }
    )


def test_prediction_semantic_hash_recovers_from_raw_provenance_after_snapshot_overwrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert DATABASE_URL is not None
    module = importlib.import_module("bp_engine.live_prediction.cli")
    policy = _policy()
    start = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    end = start + timedelta(minutes=5)
    scheduled = start + timedelta(seconds=240)
    up_original = _book_event(
        asset_id="up-provenance-recovery",
        received_at=scheduled - timedelta(milliseconds=200),
        bid="0.58",
        ask="0.60",
    )
    down_original = _book_event(
        asset_id="down-provenance-recovery",
        received_at=scheduled - timedelta(milliseconds=150),
        bid="0.40",
        ask="0.42",
    )
    up_late = _book_event(
        asset_id="up-provenance-recovery",
        received_at=scheduled + timedelta(milliseconds=200),
        bid="0.57",
        ask="0.61",
    )
    repository = RecorderRepository()
    reducer = MarketStateReducer()
    engine = create_engine(DATABASE_URL)
    schema.metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            delete(schema.market_state_1s).where(
                schema.market_state_1s.c.instrument == "condition-provenance-recovery"
            )
        )
        connection.execute(
            delete(schema.raw_market_events).where(
                schema.raw_market_events.c.instrument == "condition-provenance-recovery"
            )
        )
        repository.insert_events(connection, (up_original, down_original))
        reducer.observe(up_original)
        reducer.observe(down_original)
        repository.upsert_state_snapshots(connection, reducer.snapshots(scheduled))

        reader = FeatureSourceReader()
        up_state = reader.latest_state(
            connection,
            source="polymarket",
            stream="market",
            instrument="condition-provenance-recovery",
            asset_id="up-provenance-recovery",
            feature_at=scheduled,
        )
        down_state = reader.latest_state(
            connection,
            source="polymarket",
            stream="market",
            instrument="condition-provenance-recovery",
            asset_id="down-provenance-recovery",
            feature_at=scheduled,
        )
        assert up_state is not None
        assert down_state is not None

        probability = 0.5788541926934843
        predictors = _merge_book_predictors(
            probability,
            book_state("pm_up", up_state),
            book_state("pm_down", down_state),
        )
        fingerprint = _input_fingerprint(
            start=start,
            end=end,
            scheduled=scheduled,
            probability=probability,
            up_state=up_state,
            down_state=down_state,
        )
        live_input = LiveMarketInput(
            condition_id="condition-provenance-recovery",
            up_token_id="up-provenance-recovery",
            down_token_id="down-provenance-recovery",
            market_start_at=start,
            market_end_at=end,
            scheduled_at=scheduled,
            downloaded_at=scheduled + timedelta(seconds=1),
            price_source="polymarket_clob",
            price_dataset="prices_history",
            price_request_params={
                "market": "up-provenance-recovery",
                "startTs": str(int(start.timestamp())),
                "endTs": str(int(scheduled.timestamp())),
                "fidelity": "1",
            },
            price_response_sha256="5" * 64,
            price_response_payload={},
            market_probability_observed=True,
            market_probability=probability,
            market_probability_observed_at=scheduled,
            up_book=_book_input(up_state),
            down_book=_book_input(down_state),
            predictors=predictors,
            input_fingerprint=fingerprint,
        )
        prediction = build_live_prediction(
            policy,
            live_input,
            condition_id="condition-provenance-recovery",
            slug="btc-updown-5m-condition-provenance-recovery",
            horizon_seconds=300,
            market_start_at=start,
            market_end_at=end,
            up_token_id="up-provenance-recovery",
            down_token_id="down-provenance-recovery",
            recorded_at=scheduled + timedelta(seconds=2),
        )

        repository.insert_events(connection, (up_late,))
        reducer.observe(up_late)
        repository.upsert_state_snapshots(connection, reducer.snapshots(scheduled))
        current_up = reader.latest_state(
            connection,
            source="polymarket",
            stream="market",
            instrument="condition-provenance-recovery",
            asset_id="up-provenance-recovery",
            feature_at=scheduled,
        )
        assert current_up is None or current_up.effective_at != prediction.up_book_cutoff_at

        monkeypatch.setattr(module, "_semantic_hash_matches", lambda *_args: False)
        assert module._prediction_semantic_hash_matches(
            connection,
            asdict(prediction),
            policy,
        ) is True
