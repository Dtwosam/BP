from __future__ import annotations

import importlib
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, delete, insert, select

from bp_engine.labels.models import MarketLabel
from bp_engine.live_prediction.models import LivePrediction
from bp_engine.live_prediction.repository import LivePredictionRepository
from bp_engine.storage import schema

DATABASE_URL = os.getenv("BP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BP_TEST_DATABASE_URL is required for PostgreSQL integration coverage",
)


def _prediction() -> LivePrediction:
    start = datetime(2026, 8, 26, 17, 0, tzinfo=UTC)
    scheduled = start + timedelta(minutes=4)
    recorded = scheduled + timedelta(seconds=1)
    return LivePrediction(
        prediction_id="d" * 64,
        semantic_sha256="1" * 64,
        prediction_version="live-prediction-v1",
        live_input_version="phase10-live-market-input-v1",
        condition_id="phase10-evaluation-postgres",
        slug="btc-updown-5m-phase10-evaluation-postgres",
        horizon_seconds=300,
        market_start_at=start,
        market_end_at=start + timedelta(minutes=5),
        scheduled_at=scheduled,
        recorded_at=recorded,
        lateness_ms=1000,
        up_token_id="pg-up",
        down_token_id="pg-down",
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
        edge_policy="trade_threshold",
        min_edge=0.02,
        training_prior=0.48,
        raw_probability=0.62,
        calibrated_probability=0.64,
        predicted_target=1,
        predicted_side="up",
        market_probability_observed=True,
        market_probability=0.62,
        market_probability_observed_at=scheduled,
        market_probability_downloaded_at=scheduled + timedelta(milliseconds=500),
        market_probability_source="polymarket_clob",
        market_probability_dataset="prices_history",
        market_probability_request_params={"market": "pg-up", "fidelity": "1"},
        market_probability_response_sha256="8" * 64,
        up_best_bid=0.54,
        up_best_ask=0.56,
        up_book_cutoff_at=scheduled,
        up_book_fresh=True,
        down_best_bid=0.44,
        down_best_ask=0.46,
        down_book_cutoff_at=scheduled,
        down_book_fresh=True,
        selected_side="up",
        executable=True,
        trade=True,
        decision_reason="trade",
        selected_ask=0.56,
        selected_bid=0.54,
        selected_spread=0.02,
        fee=0.017248,
        slippage_buffer=0.01,
        raw_edge=0.08,
        cost_adjusted_edge=0.052752,
        decision_min_edge=0.02,
        edge_decision={"side": "up", "trade": True},
        input_fingerprint="9" * 64,
    )


def _label(prediction: LivePrediction) -> MarketLabel:
    return MarketLabel(
        condition_id=prediction.condition_id,
        gamma_market_id="gamma-evaluation-postgres",
        slug=prediction.slug,
        horizon_seconds=prediction.horizon_seconds,
        market_start_at=prediction.market_start_at,
        market_end_at=prediction.market_end_at,
        official_outcome="Down",
        start_reference=None,
        end_reference=None,
        resolution_source="official-rules",
        rules_hash="rules-hash",
        label_source="polymarket_gamma_snapshot",
        label_version="official-outcome-v1",
        source_snapshot_sha256="a" * 64,
        source_observed_at=prediction.market_end_at + timedelta(seconds=15),
        generated_at=prediction.market_end_at + timedelta(seconds=20),
    )


def test_postgres_append_evaluation_is_idempotent_and_parent_is_immutable() -> None:
    assert DATABASE_URL is not None
    module = importlib.import_module("bp_engine.live_prediction.evaluation")
    engine = create_engine(DATABASE_URL)
    schema.metadata.create_all(engine)
    prediction = _prediction()
    label = _label(prediction)

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
        connection.execute(
            delete(schema.market_labels).where(
                schema.market_labels.c.condition_id == prediction.condition_id
            )
        )
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
        stored = connection.execute(
            select(schema.live_prediction_evaluations).where(
                schema.live_prediction_evaluations.c.prediction_id == prediction.prediction_id
            )
        ).mappings().one()
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
        connection.execute(
            delete(schema.market_labels).where(
                schema.market_labels.c.condition_id == prediction.condition_id
            )
        )

    assert first.created == 1 and first.existing == 0
    assert second.created == 0 and second.existing == 1
    assert stored["official_target"] == 0
    assert stored["correct"] is False
    assert before == after
