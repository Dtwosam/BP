from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta

from bp_engine.calibration.models import CalibrationFit, EdgeConfig
from bp_engine.live_prediction.models import LivePolicySpec


def _policy() -> LivePolicySpec:
    return LivePolicySpec(
        source_calibration_run_id="phase9-300",
        source_calibration_semantic_sha256="1" * 64,
        source_backtest_run_id="phase8-300",
        source_backtest_semantic_sha256="2" * 64,
        source_training_run_id="phase7-300",
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
            min_edge_grid=(0.02,),
            min_validation_trades=1,
        ),
        edge_policy="trade_threshold",
        min_edge=0.02,
        training_prior=0.48,
        policy_sha256="4" * 64,
    )


def test_default_predictor_forwards_required_market_identity(monkeypatch) -> None:
    module = importlib.import_module("bp_engine.live_prediction.service")
    policy = _policy()
    live_input = object()
    start = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    end = start + timedelta(minutes=5)
    scheduled = start + timedelta(seconds=240)
    recorded = scheduled + timedelta(seconds=1)
    expected = object()
    received: dict[str, object] = {}

    def fake_builder(
        policy_arg,
        live_input_arg,
        *,
        condition_id,
        slug,
        horizon_seconds,
        market_start_at,
        market_end_at,
        up_token_id,
        down_token_id,
        recorded_at,
    ):
        received.update(
            {
                "policy": policy_arg,
                "live_input": live_input_arg,
                "condition_id": condition_id,
                "slug": slug,
                "horizon_seconds": horizon_seconds,
                "market_start_at": market_start_at,
                "market_end_at": market_end_at,
                "up_token_id": up_token_id,
                "down_token_id": down_token_id,
                "recorded_at": recorded_at,
            }
        )
        return expected

    monkeypatch.setattr(module, "build_live_prediction", fake_builder)

    result = module._default_predictor(
        policy,
        live_input,
        condition_id="condition-live",
        slug="btc-updown-condition-live",
        horizon_seconds=300,
        market_start_at=start,
        market_end_at=end,
        scheduled_at=scheduled,
        up_token_id="up-token",
        down_token_id="down-token",
        recorded_at=recorded,
    )

    assert result is expected
    assert received == {
        "policy": policy,
        "live_input": live_input,
        "condition_id": "condition-live",
        "slug": "btc-updown-condition-live",
        "horizon_seconds": 300,
        "market_start_at": start,
        "market_end_at": end,
        "up_token_id": "up-token",
        "down_token_id": "down-token",
        "recorded_at": recorded,
    }
