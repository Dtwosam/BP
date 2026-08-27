from __future__ import annotations

import importlib.util
import sys
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bp_engine.calibration.models import CalibrationFit, EdgeConfig
from bp_engine.features.calculators import book_state
from bp_engine.features.sources import StateObservation
from bp_engine.live_prediction.inputs import LiveMarketInput, _book_input, _merge_book_predictors
from bp_engine.live_prediction.models import LivePolicySpec
from bp_engine.live_prediction.predictor import build_live_prediction


def _diagnostic_module():
    path = Path("scripts/deploy/phase10_semantic_hash_diagnostic.py")
    spec = importlib.util.spec_from_file_location("phase10_semantic_hash_diagnostic", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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


def _state(*, asset_id: str, scheduled: datetime, bid: float, ask: float) -> StateObservation:
    return StateObservation(
        row_id=1,
        bucket_at=scheduled,
        state_key=f"polymarket/market/condition/{asset_id}",
        source="polymarket",
        stream="market",
        instrument="condition",
        market_id=None,
        asset_id=asset_id,
        last_event_at=scheduled,
        state={"best_bid": bid, "best_ask": ask},
        fresh=True,
        age_seconds=0.0,
    )


def test_rebuilt_prediction_recovers_original_semantic_hash() -> None:
    module = _diagnostic_module()
    policy = _policy()
    start = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    end = start + timedelta(minutes=5)
    scheduled = start + timedelta(seconds=240)
    recorded = scheduled + timedelta(seconds=2)
    up_state = _state(asset_id="up", scheduled=scheduled, bid=0.58, ask=0.60)
    down_state = _state(asset_id="down", scheduled=scheduled, bid=0.40, ask=0.42)
    raw_probability = 0.72
    predictors = _merge_book_predictors(
        raw_probability,
        book_state("pm_up", up_state),
        book_state("pm_down", down_state),
    )
    live_input = LiveMarketInput(
        condition_id="condition",
        up_token_id="up",
        down_token_id="down",
        market_start_at=start,
        market_end_at=end,
        scheduled_at=scheduled,
        downloaded_at=scheduled + timedelta(seconds=1),
        price_source="polymarket_clob",
        price_dataset="prices_history",
        price_request_params={
            "market": "up",
            "startTs": str(int(start.timestamp())),
            "endTs": str(int(scheduled.timestamp())),
            "fidelity": "1",
        },
        price_response_sha256="5" * 64,
        price_response_payload={},
        market_probability_observed=True,
        market_probability=raw_probability,
        market_probability_observed_at=scheduled,
        up_book=_book_input(up_state),
        down_book=_book_input(down_state),
        predictors=predictors,
        input_fingerprint="6" * 64,
    )
    original = build_live_prediction(
        policy,
        live_input,
        condition_id="condition",
        slug="btc-updown-5m-condition",
        horizon_seconds=300,
        market_start_at=start,
        market_end_at=end,
        up_token_id="up",
        down_token_id="down",
        recorded_at=recorded,
    )

    rebuilt = module._rebuilt_prediction(
        asdict(original),
        policy,
        raw_probability=raw_probability,
        up_state=up_state,
        down_state=down_state,
    )

    assert rebuilt.semantic_sha256 == original.semantic_sha256
    assert rebuilt.edge_decision == original.edge_decision
