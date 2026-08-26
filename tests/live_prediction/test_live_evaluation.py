from __future__ import annotations

import importlib
import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, insert, select

from bp_engine.labels.models import MarketLabel
from bp_engine.live_prediction.models import LivePrediction
from bp_engine.live_prediction.repository import LivePredictionRepository
from bp_engine.storage import schema


def _prediction(*, trade: bool = True) -> LivePrediction:
    start = datetime(2026, 8, 26, 16, 0, tzinfo=UTC)
    scheduled = start + timedelta(minutes=4)
    recorded = scheduled + timedelta(seconds=2)
    return LivePrediction(
        prediction_id="e" * 64,
        semantic_sha256="1" * 64,
        prediction_version="live-prediction-v1",
        live_input_version="phase10-live-market-input-v1",
        condition_id="phase10-evaluation",
        slug="btc-updown-5m-phase10-evaluation",
        horizon_seconds=300,
        market_start_at=start,
        market_end_at=start + timedelta(minutes=5),
        scheduled_at=scheduled,
        recorded_at=recorded,
        lateness_ms=2000,
        up_token_id="up-token",
        down_token_id="down-token",
        source_calibration_run_id="phase9-source",
        source_calibration_semantic_sha256="2" * 64,
        source_backtest_run_id="phase8-source",
        source_backtest_semantic_sha256="3" * 64,
        source_training_run_id="phase7-source",
        source_training_semantic_sha256="4" * 64,
        calibration_version="platt-or-identity-v1",
        edge_policy_version="selected-ask-edge-v1",
        source_feature_version="core-v1",
        source_label_version="official-outcome-v1",
        selected_offset_seconds=240,
        policy_sha256="5" * 64,
        calibration_fit={"method": "identity", "intercept": None, "coefficient": None},
        calibration_fit_sha256="6" * 64,
        edge_config={"fee_rate": 0.07, "slippage_buffer": 0.01},
        edge_config_sha256="7" * 64,
        edge_policy="trade_threshold" if trade else "no_trade",
        min_edge=0.02 if trade else None,
        training_prior=0.48,
        raw_probability=0.68,
        calibrated_probability=0.70,
        predicted_target=1,
        predicted_side="up",
        market_probability_observed=True,
        market_probability=0.68,
        market_probability_observed_at=scheduled,
        market_probability_downloaded_at=scheduled + timedelta(seconds=1),
        market_probability_source="polymarket_clob",
        market_probability_dataset="prices_history",
        market_probability_request_params={"market": "up-token", "fidelity": "1"},
        market_probability_response_sha256="8" * 64,
        up_best_bid=0.58,
        up_best_ask=0.60,
        up_book_cutoff_at=scheduled,
        up_book_fresh=True,
        down_best_bid=0.40,
        down_best_ask=0.42,
        down_book_cutoff_at=scheduled,
        down_book_fresh=True,
        selected_side="up",
        executable=True,
        trade=trade,
        decision_reason="trade" if trade else "policy_no_trade",
        selected_ask=0.60,
        selected_bid=0.58,
        selected_spread=0.02,
        fee=0.0168,
        slippage_buffer=0.01,
        raw_edge=0.10,
        cost_adjusted_edge=0.0732,
        decision_min_edge=0.02 if trade else None,
        edge_decision={"side": "up", "trade": trade},
        input_fingerprint="9" * 64,
    )


def _label(*, outcome: str = "Up") -> MarketLabel:
    prediction = _prediction()
    return MarketLabel(
        condition_id=prediction.condition_id,
        gamma_market_id="gamma-evaluation",
        slug=prediction.slug,
        horizon_seconds=prediction.horizon_seconds,
        market_start_at=prediction.market_start_at,
        market_end_at=prediction.market_end_at,
        official_outcome=outcome,
        start_reference=None,
        end_reference=None,
        resolution_source="official-rules",
        rules_hash="rules-hash",
        label_source="polymarket_gamma_snapshot",
        label_version="official-outcome-v1",
        source_snapshot_sha256="a" * 64,
        source_observed_at=prediction.market_end_at + timedelta(seconds=20),
        generated_at=prediction.market_end_at + timedelta(seconds=30),
    )


def _module():
    return importlib.import_module("bp_engine.live_prediction.evaluation")


def test_evaluate_prediction_computes_metrics_and_trade_pnl() -> None:
    module = _module()
    prediction = _prediction(trade=True)
    label = _label(outcome="Up")
    evaluated_at = label.source_observed_at + timedelta(seconds=5)

    evaluation = module.evaluate_prediction(prediction, label, evaluated_at=evaluated_at)

    assert evaluation.official_target == 1
    assert evaluation.correct is True
    assert evaluation.raw_log_loss == pytest.approx(-math.log(0.68))
    assert evaluation.raw_brier == pytest.approx((0.68 - 1.0) ** 2)
    assert evaluation.calibrated_log_loss == pytest.approx(-math.log(0.70))
    assert evaluation.calibrated_brier == pytest.approx((0.70 - 1.0) ** 2)
    assert evaluation.hypothetical_gross_pnl == pytest.approx(0.40)
    assert evaluation.hypothetical_assumed_cost_pnl == pytest.approx(0.3732)
    assert len(evaluation.semantic_sha256) == 64


def test_non_trade_prediction_has_no_hypothetical_pnl() -> None:
    module = _module()
    prediction = _prediction(trade=False)
    label = _label(outcome="Down")

    evaluation = module.evaluate_prediction(
        prediction,
        label,
        evaluated_at=label.source_observed_at,
    )

    assert evaluation.correct is False
    assert evaluation.hypothetical_gross_pnl is None
    assert evaluation.hypothetical_assumed_cost_pnl is None


@pytest.mark.parametrize(
    ("mutator", "match"),
    [
        (lambda label: replace(label, condition_id="wrong"), "condition_id"),
        (lambda label: replace(label, horizon_seconds=900), "horizon_seconds"),
        (
            lambda label: replace(
                label,
                market_start_at=label.market_start_at + timedelta(seconds=1),
            ),
            "market_start_at",
        ),
        (
            lambda label: replace(
                label,
                market_end_at=label.market_end_at + timedelta(seconds=1),
            ),
            "market_end_at",
        ),
    ],
)
def test_evaluation_requires_exact_prediction_label_identity(mutator, match: str) -> None:
    module = _module()
    prediction = _prediction()
    label = mutator(_label())

    with pytest.raises(module.EvaluationIntegrityError, match=match):
        module.evaluate_prediction(prediction, label, evaluated_at=label.source_observed_at)


def test_evaluation_rejects_pre_end_or_pre_prediction_label_observation() -> None:
    module = _module()
    prediction = _prediction()
    label = _label()

    with pytest.raises(module.EvaluationIntegrityError, match="market_end"):
        module.evaluate_prediction(
            prediction,
            replace(label, source_observed_at=prediction.market_end_at - timedelta(seconds=1)),
            evaluated_at=prediction.market_end_at,
        )

    invalid_prediction = replace(
        prediction,
        recorded_at=label.source_observed_at,
        lateness_ms=int(
            (label.source_observed_at - prediction.scheduled_at).total_seconds() * 1000
        ),
    )
    with pytest.raises(module.EvaluationIntegrityError, match="source_observed_at"):
        module.evaluate_prediction(
            invalid_prediction,
            label,
            evaluated_at=label.source_observed_at,
        )


def test_evaluation_rejects_prediction_at_or_after_market_end() -> None:
    module = _module()
    prediction = _prediction()
    label = _label()
    invalid = replace(
        prediction,
        recorded_at=prediction.market_end_at,
        lateness_ms=int(
            (prediction.market_end_at - prediction.scheduled_at).total_seconds() * 1000
        ),
    )

    with pytest.raises(module.EvaluationIntegrityError, match="recorded_at"):
        module.evaluate_prediction(invalid, label, evaluated_at=label.source_observed_at)


def test_append_available_evaluations_is_idempotent_and_keeps_parent_unchanged() -> None:
    module = _module()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    schema.metadata.create_all(engine)
    prediction = _prediction()
    label = _label()

    with engine.begin() as connection:
        LivePredictionRepository().store(connection, prediction)
        connection.execute(insert(schema.market_labels).values(**label.__dict__))
        before = dict(
            connection.execute(
                select(schema.live_predictions).where(
                    schema.live_predictions.c.prediction_id == prediction.prediction_id
                )
            ).mappings().one()
        )
        first = module.append_available_evaluations(
            connection,
            evaluated_at=label.generated_at,
        )
        second = module.append_available_evaluations(
            connection,
            evaluated_at=label.generated_at + timedelta(seconds=1),
        )
        after = dict(
            connection.execute(
                select(schema.live_predictions).where(
                    schema.live_predictions.c.prediction_id == prediction.prediction_id
                )
            ).mappings().one()
        )

    assert first.created == 1
    assert first.existing == 0
    assert second.created == 0
    assert second.existing == 1
    assert before == after


def test_append_available_evaluations_rejects_contradictory_existing_evaluation() -> None:
    module = _module()
    repository = importlib.import_module("bp_engine.live_prediction.repository")
    engine = create_engine("sqlite+pysqlite:///:memory:")
    schema.metadata.create_all(engine)
    prediction = _prediction()
    label = _label()

    with engine.begin() as connection:
        LivePredictionRepository().store(connection, prediction)
        connection.execute(insert(schema.market_labels).values(**label.__dict__))
        module.append_available_evaluations(connection, evaluated_at=label.generated_at)
        connection.execute(
            schema.live_prediction_evaluations.update()
            .where(
                schema.live_prediction_evaluations.c.prediction_id
                == prediction.prediction_id
            )
            .values(official_outcome="Down", official_target=0)
        )
        with pytest.raises(repository.LivePredictionEvaluationConflict):
            module.append_available_evaluations(
                connection,
                evaluated_at=label.generated_at,
            )
