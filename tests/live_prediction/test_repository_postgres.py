from __future__ import annotations

import importlib
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, delete, select

from bp_engine.storage import schema

DATABASE_URL = os.getenv("BP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BP_TEST_DATABASE_URL is required for PostgreSQL integration coverage",
)


def _prediction():
    models = importlib.import_module("bp_engine.live_prediction.models")
    start = datetime(2026, 8, 26, 15, 0, tzinfo=UTC)
    scheduled = start + timedelta(minutes=4)
    recorded = scheduled + timedelta(seconds=2)
    return models.LivePrediction(
        prediction_id="e" * 64,
        semantic_sha256="1" * 64,
        prediction_version="live-prediction-v1",
        live_input_version="phase10-live-market-input-v1",
        condition_id="phase10-ledger-postgres",
        slug="btc-updown-5m-phase10-ledger-postgres",
        horizon_seconds=300,
        market_start_at=start,
        market_end_at=start + timedelta(minutes=5),
        scheduled_at=scheduled,
        recorded_at=recorded,
        lateness_ms=2000,
        up_token_id="pg-up-token",
        down_token_id="pg-down-token",
        source_calibration_run_id="phase9-pg",
        source_calibration_semantic_sha256="2" * 64,
        source_backtest_run_id="phase8-pg",
        source_backtest_semantic_sha256="3" * 64,
        source_training_run_id="phase7-pg",
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
        edge_policy="no_trade",
        min_edge=None,
        training_prior=0.48,
        raw_probability=0.48,
        calibrated_probability=0.48,
        predicted_target=0,
        predicted_side="down",
        market_probability_observed=False,
        market_probability=None,
        market_probability_observed_at=None,
        market_probability_downloaded_at=scheduled + timedelta(seconds=1),
        market_probability_source="polymarket_clob",
        market_probability_dataset="prices_history",
        market_probability_request_params={"market": "pg-up-token", "fidelity": "1"},
        market_probability_response_sha256="8" * 64,
        up_best_bid=0.58,
        up_best_ask=0.60,
        up_book_cutoff_at=scheduled,
        up_book_fresh=True,
        down_best_bid=0.40,
        down_best_ask=0.42,
        down_book_cutoff_at=scheduled,
        down_book_fresh=True,
        selected_side="down",
        executable=True,
        trade=False,
        decision_reason="policy_no_trade",
        selected_ask=0.42,
        selected_bid=0.40,
        selected_spread=0.02,
        fee=0.017052,
        slippage_buffer=0.01,
        raw_edge=0.10,
        cost_adjusted_edge=0.072948,
        decision_min_edge=None,
        edge_decision={"side": "down", "trade": False, "reason": "policy_no_trade"},
        input_fingerprint="9" * 64,
    )


def _evaluation():
    models = importlib.import_module("bp_engine.live_prediction.models")
    return models.LivePredictionEvaluation(
        prediction_id="e" * 64,
        label_version="official-outcome-v1",
        official_outcome="Down",
        official_target=0,
        label_source="polymarket_gamma_snapshot",
        label_source_snapshot_sha256="a" * 64,
        label_source_observed_at=datetime(2026, 8, 26, 15, 5, 30, tzinfo=UTC),
        evaluated_at=datetime(2026, 8, 26, 15, 6, tzinfo=UTC),
        correct=True,
        raw_log_loss=0.653926,
        raw_brier=0.2304,
        calibrated_log_loss=0.653926,
        calibrated_brier=0.2304,
        hypothetical_gross_pnl=None,
        hypothetical_assumed_cost_pnl=None,
        semantic_sha256="b" * 64,
    )


def test_postgres_live_ledgers_are_idempotent_and_parent_is_immutable() -> None:
    assert DATABASE_URL is not None
    module = importlib.import_module("bp_engine.live_prediction.repository")
    engine = create_engine(DATABASE_URL)
    schema.metadata.create_all(engine)
    prediction_repository = module.LivePredictionRepository()
    evaluation_repository = module.LivePredictionEvaluationRepository()
    prediction = _prediction()
    evaluation = _evaluation()

    with engine.begin() as connection:
        connection.execute(
            delete(schema.live_prediction_evaluations).where(
                schema.live_prediction_evaluations.c.prediction_id == prediction.prediction_id
            )
        )
        connection.execute(
            delete(schema.live_predictions).where(
                schema.live_predictions.c.condition_id == prediction.condition_id
            )
        )
        first = prediction_repository.store(connection, prediction)
        before = dict(
            connection.execute(
                select(schema.live_predictions).where(
                    schema.live_predictions.c.prediction_id == prediction.prediction_id
                )
            ).mappings().one()
        )
        second = prediction_repository.store(connection, prediction)
        evaluation_first = evaluation_repository.store(connection, evaluation)
        evaluation_second = evaluation_repository.store(connection, evaluation)
        after = dict(
            connection.execute(
                select(schema.live_predictions).where(
                    schema.live_predictions.c.prediction_id == prediction.prediction_id
                )
            ).mappings().one()
        )
        with pytest.raises(module.LivePredictionConflict):
            prediction_repository.store(
                connection,
                replace(prediction, semantic_sha256="c" * 64),
            )
        with pytest.raises(module.LivePredictionEvaluationConflict):
            evaluation_repository.store(
                connection,
                replace(evaluation, semantic_sha256="d" * 64),
            )
        connection.execute(
            delete(schema.live_prediction_evaluations).where(
                schema.live_prediction_evaluations.c.prediction_id == prediction.prediction_id
            )
        )
        connection.execute(
            delete(schema.live_predictions).where(
                schema.live_predictions.c.condition_id == prediction.condition_id
            )
        )

    assert first.created is True and first.existing is False
    assert second.created is False and second.existing is True
    assert evaluation_first.created is True and evaluation_first.existing is False
    assert evaluation_second.created is False and evaluation_second.existing is True
    assert before == after
