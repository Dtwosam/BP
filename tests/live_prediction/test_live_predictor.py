from __future__ import annotations

import importlib
import inspect
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from bp_engine.calibration.calibrators import apply_calibration
from bp_engine.calibration.models import CalibrationFit, EdgeConfig
from bp_engine.live_prediction.inputs import LiveBookInput, LiveMarketInput
from bp_engine.live_prediction.models import LivePolicySpec


def _module():
    return importlib.import_module("bp_engine.live_prediction.predictor")


def _times(*, offset_seconds: int = 240) -> tuple[datetime, datetime, datetime]:
    start = datetime(2026, 8, 26, 16, 0, tzinfo=UTC)
    end = start + timedelta(minutes=5)
    scheduled = start + timedelta(seconds=offset_seconds)
    return start, end, scheduled


def _edge_config() -> EdgeConfig:
    return EdgeConfig(
        fee_rate=0.07,
        slippage_buffer=0.01,
        min_edge_grid=(0.0, 0.02, 0.05),
        min_validation_trades=3,
        max_spread=None,
    )


def _policy(
    *,
    calibration_fit: CalibrationFit | None = None,
    edge_policy: str = "trade_threshold",
    min_edge: float | None = 0.02,
    training_prior: float = 0.48,
    offset_seconds: int = 240,
) -> LivePolicySpec:
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
        selected_offset_seconds=offset_seconds,
        calibration_fit=calibration_fit
        or CalibrationFit(method="identity", intercept=None, coefficient=None),
        edge_config=_edge_config(),
        edge_policy=edge_policy,
        min_edge=min_edge,
        training_prior=training_prior,
        policy_sha256="4" * 64,
    )


def _book(
    *,
    asset_id: str,
    scheduled_at: datetime,
    bid: float,
    ask: float,
) -> LiveBookInput:
    return LiveBookInput(
        asset_id=asset_id,
        state_key=f"polymarket/market/condition-predictor/{asset_id}",
        bucket_at=scheduled_at,
        last_event_at=scheduled_at,
        fresh=True,
        age_seconds=0.0,
        state={"best_bid": bid, "best_ask": ask},
    )


def _live_input(
    *,
    probability: float | None = 0.72,
    offset_seconds: int = 240,
) -> LiveMarketInput:
    start, end, scheduled = _times(offset_seconds=offset_seconds)
    observed = probability is not None
    return LiveMarketInput(
        condition_id="condition-predictor",
        up_token_id="up-token",
        down_token_id="down-token",
        market_start_at=start,
        market_end_at=end,
        scheduled_at=scheduled,
        downloaded_at=scheduled + timedelta(seconds=1),
        price_source="polymarket_clob",
        price_dataset="prices_history",
        price_request_params={
            "market": "up-token",
            "startTs": str(int(start.timestamp())),
            "endTs": str(int(scheduled.timestamp())),
            "fidelity": "1",
        },
        price_response_sha256="5" * 64,
        price_response_payload={"history": []},
        market_probability_observed=observed,
        market_probability=probability,
        market_probability_observed_at=scheduled if observed else None,
        up_book=_book(
            asset_id="up-token",
            scheduled_at=scheduled,
            bid=0.58,
            ask=0.60,
        ),
        down_book=_book(
            asset_id="down-token",
            scheduled_at=scheduled,
            bid=0.40,
            ask=0.42,
        ),
        predictors={
            "pm_up_price": probability,
            "pm_up_best_bid": 0.58,
            "pm_up_best_ask": 0.60,
            "pm_down_best_bid": 0.40,
            "pm_down_best_ask": 0.42,
            "missing__pm_up_book_missing": 0.0,
            "missing__pm_up_book_stale": 0.0,
            "missing__pm_down_book_missing": 0.0,
            "missing__pm_down_book_stale": 0.0,
        },
        input_fingerprint="6" * 64,
    )


def _build(module, policy: LivePolicySpec, live_input: LiveMarketInput, recorded_at: datetime):
    return module.build_live_prediction(
        policy,
        live_input,
        condition_id="condition-predictor",
        slug="btc-updown-5m-condition-predictor",
        horizon_seconds=300,
        market_start_at=live_input.market_start_at,
        market_end_at=live_input.market_end_at,
        up_token_id="up-token",
        down_token_id="down-token",
        recorded_at=recorded_at,
    )


def test_observed_market_price_drives_identity_prediction_and_phase9_edge() -> None:
    module = _module()
    policy = _policy()
    live_input = _live_input(probability=0.72)
    prediction = _build(
        module,
        policy,
        live_input,
        live_input.scheduled_at + timedelta(seconds=2),
    )

    assert prediction.raw_probability == pytest.approx(0.72)
    assert prediction.calibrated_probability == pytest.approx(0.72)
    assert prediction.predicted_target == 1
    assert prediction.predicted_side == "up"
    assert prediction.selected_side == "up"
    assert prediction.executable is True
    assert prediction.trade is True
    assert prediction.decision_reason == "trade"
    assert prediction.selected_ask == pytest.approx(0.60)
    assert prediction.selected_bid == pytest.approx(0.58)
    assert prediction.fee == pytest.approx(0.07 * 0.60 * 0.40)
    assert prediction.cost_adjusted_edge == pytest.approx(
        0.72 - 0.60 - prediction.fee - 0.01
    )


def test_missing_market_price_uses_training_prior_but_edge_stays_unavailable() -> None:
    module = _module()
    policy = _policy(training_prior=0.48)
    live_input = _live_input(probability=None)
    prediction = _build(
        module,
        policy,
        live_input,
        live_input.scheduled_at + timedelta(seconds=2),
    )

    assert prediction.raw_probability == pytest.approx(0.48)
    assert prediction.calibrated_probability == pytest.approx(0.48)
    assert prediction.predicted_target == 0
    assert prediction.predicted_side == "down"
    assert prediction.market_probability_observed is False
    assert prediction.market_probability is None
    assert prediction.executable is False
    assert prediction.trade is False
    assert prediction.decision_reason == "missing_market_probability"


def test_platt_calibration_can_select_down_side_without_target_input() -> None:
    module = _module()
    fit = CalibrationFit(method="platt", intercept=0.0, coefficient=2.0)
    policy = _policy(calibration_fit=fit)
    live_input = _live_input(probability=0.30)
    prediction = _build(
        module,
        policy,
        live_input,
        live_input.scheduled_at + timedelta(seconds=1),
    )
    expected = apply_calibration(fit, (0.30,))[0]

    assert prediction.raw_probability == pytest.approx(0.30)
    assert prediction.calibrated_probability == pytest.approx(expected)
    assert prediction.predicted_target == 0
    assert prediction.predicted_side == "down"
    assert prediction.selected_side == "down"
    assert prediction.selected_ask == pytest.approx(0.42)


def test_frozen_no_trade_policy_remains_non_trading_when_executable() -> None:
    module = _module()
    policy = _policy(edge_policy="no_trade", min_edge=None)
    live_input = _live_input(probability=0.30)
    prediction = _build(
        module,
        policy,
        live_input,
        live_input.scheduled_at + timedelta(seconds=1),
    )

    assert prediction.executable is True
    assert prediction.trade is False
    assert prediction.decision_reason == "policy_no_trade"
    assert prediction.decision_min_edge is None


def test_prediction_carries_complete_frozen_source_and_input_provenance() -> None:
    module = _module()
    policy = _policy()
    live_input = _live_input()
    prediction = _build(
        module,
        policy,
        live_input,
        live_input.scheduled_at + timedelta(seconds=3),
    )

    assert prediction.prediction_version == "live-prediction-v1"
    assert prediction.live_input_version == "phase10-live-market-input-v1"
    assert prediction.source_calibration_run_id == policy.source_calibration_run_id
    assert prediction.source_calibration_semantic_sha256 == (
        policy.source_calibration_semantic_sha256
    )
    assert prediction.source_backtest_run_id == policy.source_backtest_run_id
    assert prediction.source_training_run_id == policy.source_training_run_id
    assert prediction.policy_sha256 == policy.policy_sha256
    assert prediction.input_fingerprint == live_input.input_fingerprint
    assert prediction.market_probability_response_sha256 == live_input.price_response_sha256
    assert prediction.market_probability_request_params == live_input.price_request_params
    assert len(prediction.calibration_fit_sha256) == 64
    assert len(prediction.edge_config_sha256) == 64
    assert len(prediction.prediction_id) == 64
    assert len(prediction.semantic_sha256) == 64


def test_prediction_id_is_natural_identity_while_semantic_hash_covers_evidence() -> None:
    module = _module()
    policy = _policy()
    live_input = _live_input()
    recorded_at = live_input.scheduled_at + timedelta(seconds=2)
    first = _build(module, policy, live_input, recorded_at)
    second = _build(module, policy, live_input, recorded_at)
    changed_input = replace(live_input, input_fingerprint="7" * 64)
    changed = _build(module, policy, changed_input, recorded_at)

    assert first == second
    assert first.prediction_id == changed.prediction_id
    assert first.semantic_sha256 != changed.semantic_sha256


@pytest.mark.parametrize(
    ("recorded_delta", "offset_seconds"),
    [
        (timedelta(microseconds=-1), 240),
        (timedelta(seconds=10, microseconds=1), 240),
        (timedelta(seconds=1), 299),
    ],
)
def test_prediction_rejects_early_late_or_market_end_recording(
    recorded_delta: timedelta,
    offset_seconds: int,
) -> None:
    module = _module()
    policy = _policy(offset_seconds=offset_seconds)
    live_input = _live_input(offset_seconds=offset_seconds)

    with pytest.raises(module.PredictionDeadlineError):
        _build(module, policy, live_input, live_input.scheduled_at + recorded_delta)


def test_exact_ten_second_lateness_is_accepted_and_recorded() -> None:
    module = _module()
    policy = _policy()
    live_input = _live_input()
    prediction = _build(
        module,
        policy,
        live_input,
        live_input.scheduled_at + timedelta(seconds=10),
    )

    assert prediction.lateness_ms == 10_000
    assert prediction.recorded_at == live_input.scheduled_at + timedelta(seconds=10)


def test_market_policy_and_live_input_identity_must_match_exactly() -> None:
    module = _module()
    policy = _policy()
    live_input = _live_input()

    with pytest.raises(module.PredictionIntegrityError, match="condition_id"):
        module.build_live_prediction(
            policy,
            live_input,
            condition_id="wrong-condition",
            slug="btc-updown-5m-wrong-condition",
            horizon_seconds=300,
            market_start_at=live_input.market_start_at,
            market_end_at=live_input.market_end_at,
            up_token_id="up-token",
            down_token_id="down-token",
            recorded_at=live_input.scheduled_at + timedelta(seconds=1),
        )

    with pytest.raises(module.PredictionIntegrityError, match="scheduled_at"):
        _build(
            module,
            replace(
                policy,
                selected_offset_seconds=policy.selected_offset_seconds - 1,
            ),
            live_input,
            live_input.scheduled_at + timedelta(seconds=1),
        )


def test_prediction_builder_is_label_blind_and_has_no_database_dependency() -> None:
    module = _module()
    signature = inspect.signature(module.build_live_prediction)
    parameter_names = set(signature.parameters)
    source = inspect.getsource(module)

    assert "label" not in parameter_names
    assert "outcome" not in parameter_names
    assert "target" not in parameter_names
    assert "connection" not in parameter_names
    assert "market_labels" not in source
    assert "sqlalchemy" not in source
