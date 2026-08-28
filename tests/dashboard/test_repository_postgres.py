from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, delete, insert

from bp_engine.storage import schema

DATABASE_URL = os.getenv("BP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BP_TEST_DATABASE_URL is required for PostgreSQL integration coverage",
)


def _prediction_values(
    *,
    prediction_id: str,
    condition_id: str,
    slug: str,
    version: str,
    scheduled_at: datetime,
    probability: float,
) -> dict[str, object]:
    start_at = scheduled_at - timedelta(minutes=4)
    end_at = scheduled_at + timedelta(minutes=1)
    side = "up" if probability >= 0.5 else "down"
    return {
        "prediction_id": prediction_id,
        "semantic_sha256": "1" * 64,
        "prediction_version": version,
        "live_input_version": "phase10-live-market-input-v1",
        "condition_id": condition_id,
        "slug": slug,
        "horizon_seconds": 300,
        "market_start_at": start_at,
        "market_end_at": end_at,
        "scheduled_at": scheduled_at,
        "recorded_at": scheduled_at + timedelta(seconds=1),
        "lateness_ms": 1000,
        "up_token_id": "up-token",
        "down_token_id": "down-token",
        "source_calibration_run_id": "cal-run",
        "source_calibration_semantic_sha256": "2" * 64,
        "source_backtest_run_id": "bt-run",
        "source_backtest_semantic_sha256": "3" * 64,
        "source_training_run_id": "train-run",
        "source_training_semantic_sha256": "4" * 64,
        "calibration_version": "cal-v1",
        "edge_policy_version": "edge-v1",
        "source_feature_version": "feature-v1",
        "source_label_version": "label-v1",
        "selected_offset_seconds": 240,
        "policy_sha256": "5" * 64,
        "calibration_fit": {"method": "identity"},
        "calibration_fit_sha256": "6" * 64,
        "edge_config": {"fee_rate": 0.0},
        "edge_config_sha256": "7" * 64,
        "edge_policy": "threshold",
        "min_edge": 0.02,
        "training_prior": 0.5,
        "raw_probability": probability,
        "calibrated_probability": probability,
        "predicted_target": 1 if side == "up" else 0,
        "predicted_side": side,
        "market_probability_observed": True,
        "market_probability": 0.55,
        "market_probability_observed_at": scheduled_at,
        "market_probability_downloaded_at": scheduled_at,
        "market_probability_source": "polymarket_clob",
        "market_probability_dataset": "prices_history",
        "market_probability_request_params": {"market": "up-token"},
        "market_probability_response_sha256": "8" * 64,
        "up_best_bid": 0.54,
        "up_best_ask": 0.56,
        "up_book_cutoff_at": scheduled_at,
        "up_book_fresh": True,
        "down_best_bid": 0.44,
        "down_best_ask": 0.46,
        "down_book_cutoff_at": scheduled_at,
        "down_book_fresh": True,
        "selected_side": side,
        "executable": True,
        "trade": probability >= 0.6,
        "decision_reason": "edge_threshold_met" if probability >= 0.6 else "no_trade_edge",
        "selected_ask": 0.56 if side == "up" else 0.46,
        "selected_bid": 0.54 if side == "up" else 0.44,
        "selected_spread": 0.02,
        "fee": 0.0,
        "slippage_buffer": 0.01,
        "raw_edge": abs(probability - 0.55),
        "cost_adjusted_edge": abs(probability - 0.55) - 0.01,
        "decision_min_edge": 0.02,
        "edge_decision": {"side": side},
        "input_fingerprint": "9" * 64,
    }


def test_postgres_dashboard_repository_is_read_only_and_selects_current_evidence() -> None:
    assert DATABASE_URL is not None
    from bp_engine.dashboard.repository import PostgresDashboardRepository

    engine = create_engine(DATABASE_URL)
    schema.metadata.create_all(engine)
    now = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
    condition_id = "phase11-dashboard-condition"
    slug = "btc-updown-5m-phase11-dashboard"
    inactive_condition = "phase11-dashboard-inactive"
    prediction_old = "phase11-dashboard-prediction-old"
    prediction_new = "phase11-dashboard-prediction-new"

    with engine.begin() as connection:
        connection.execute(
            delete(schema.live_prediction_evaluations).where(
                schema.live_prediction_evaluations.c.prediction_id.in_(
                    [prediction_old, prediction_new]
                )
            )
        )
        connection.execute(
            delete(schema.live_predictions).where(
                schema.live_predictions.c.condition_id.in_([condition_id, inactive_condition])
            )
        )
        connection.execute(
            delete(schema.polymarket_markets).where(
                schema.polymarket_markets.c.condition_id.in_([condition_id, inactive_condition])
            )
        )
        connection.execute(
            delete(schema.feed_status).where(
                schema.feed_status.c.source == "phase11-test"
            )
        )

        connection.execute(
            insert(schema.polymarket_markets),
            [
                {
                    "gamma_market_id": "phase11-gamma-active",
                    "event_id": "phase11-event-active",
                    "condition_id": condition_id,
                    "slug": slug,
                    "question": "Will BTC go up?",
                    "horizon_seconds": 300,
                    "start_at": now - timedelta(minutes=4),
                    "end_at": now + timedelta(minutes=1),
                    "up_token_id": "up-token",
                    "down_token_id": "down-token",
                    "resolution_source": "chainlink",
                    "rules_text": "test",
                    "rules_hash": "a" * 64,
                    "active": True,
                    "closed": False,
                    "accepting_orders": True,
                    "resolved_outcome": None,
                    "discovered_at": now - timedelta(minutes=10),
                    "updated_at": now,
                },
                {
                    "gamma_market_id": "phase11-gamma-inactive",
                    "event_id": "phase11-event-inactive",
                    "condition_id": inactive_condition,
                    "slug": "btc-updown-inactive-phase11",
                    "question": "Old BTC market",
                    "horizon_seconds": 300,
                    "start_at": now - timedelta(minutes=10),
                    "end_at": now - timedelta(minutes=5),
                    "up_token_id": "old-up-token",
                    "down_token_id": "old-down-token",
                    "resolution_source": "chainlink",
                    "rules_text": "test",
                    "rules_hash": "b" * 64,
                    "active": False,
                    "closed": True,
                    "accepting_orders": False,
                    "resolved_outcome": "Down",
                    "discovered_at": now - timedelta(minutes=20),
                    "updated_at": now - timedelta(minutes=5),
                },
            ],
        )
        connection.execute(
            insert(schema.live_predictions),
            [
                _prediction_values(
                    prediction_id=prediction_old,
                    condition_id=condition_id,
                    slug=slug,
                    version="phase11-old",
                    scheduled_at=now - timedelta(seconds=30),
                    probability=0.58,
                ),
                _prediction_values(
                    prediction_id=prediction_new,
                    condition_id=condition_id,
                    slug=slug,
                    version="phase11-new",
                    scheduled_at=now - timedelta(seconds=10),
                    probability=0.66,
                ),
            ],
        )
        connection.execute(
            insert(schema.live_prediction_evaluations).values(
                prediction_id=prediction_new,
                label_version="official-outcome-v1",
                official_outcome="Up",
                official_target=1,
                label_source="polymarket_gamma_snapshot",
                label_source_snapshot_sha256="c" * 64,
                label_source_observed_at=now + timedelta(minutes=2),
                evaluated_at=now + timedelta(minutes=3),
                correct=True,
                raw_log_loss=0.4,
                raw_brier=0.12,
                calibrated_log_loss=0.415515,
                calibrated_brier=0.1156,
                hypothetical_gross_pnl=None,
                hypothetical_assumed_cost_pnl=None,
                semantic_sha256="d" * 64,
            )
        )
        connection.execute(
            insert(schema.feed_status).values(
                source="phase11-test",
                stream="spot",
                status="connected",
                last_received_at=now - timedelta(seconds=4),
                last_source_timestamp=now - timedelta(seconds=5),
                updated_at=now - timedelta(seconds=1),
                details={"test": True},
            )
        )

    repository = PostgresDashboardRepository(engine)
    active = repository.list_active_markets(now)
    health = [row for row in repository.list_feed_health(now) if row["source"] == "phase11-test"]
    history = [
        row
        for row in repository.list_predictions(limit=50)
        if row["condition_id"] == condition_id
    ]
    performance_predictions = [
        row
        for row in repository.list_performance_predictions()
        if row["condition_id"] == condition_id
    ]
    evaluations = [
        row
        for row in repository.list_evaluations()
        if row["prediction_id"] in {prediction_old, prediction_new}
    ]

    assert [row["condition_id"] for row in active if row["condition_id"] == condition_id] == [
        condition_id
    ]
    current = next(row for row in active if row["condition_id"] == condition_id)
    assert current["prediction_id"] == prediction_new
    assert float(current["calibrated_probability"]) == 0.66
    assert health[0]["age_seconds"] == 4.0
    assert [row["prediction_id"] for row in history] == [prediction_new, prediction_old]
    assert history[0]["official_outcome"] == "Up"
    assert len(performance_predictions) == 2
    assert [row["prediction_id"] for row in evaluations] == [prediction_new]

    with engine.begin() as connection:
        connection.execute(
            delete(schema.live_prediction_evaluations).where(
                schema.live_prediction_evaluations.c.prediction_id.in_(
                    [prediction_old, prediction_new]
                )
            )
        )
        connection.execute(
            delete(schema.live_predictions).where(
                schema.live_predictions.c.condition_id.in_([condition_id, inactive_condition])
            )
        )
        connection.execute(
            delete(schema.polymarket_markets).where(
                schema.polymarket_markets.c.condition_id.in_([condition_id, inactive_condition])
            )
        )
        connection.execute(
            delete(schema.feed_status).where(schema.feed_status.c.source == "phase11-test")
        )


def test_dashboard_feed_health_falls_back_to_compact_market_state() -> None:
    assert DATABASE_URL is not None
    from bp_engine.dashboard.repository import PostgresDashboardRepository

    engine = create_engine(DATABASE_URL)
    schema.metadata.create_all(engine)
    now = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
    source = "phase11-state-test"
    stream = "spot"

    with engine.begin() as connection:
        connection.execute(delete(schema.feed_status).where(schema.feed_status.c.source == source))
        connection.execute(
            delete(schema.market_state_1s).where(schema.market_state_1s.c.source == source)
        )
        connection.execute(
            insert(schema.market_state_1s).values(
                bucket_at=now - timedelta(seconds=2),
                state_key="phase11-state-test:spot:BTCUSD",
                source=source,
                stream=stream,
                instrument="BTCUSD",
                market_id=None,
                asset_id=None,
                last_event_at=now - timedelta(seconds=3),
                state={"test": True},
            )
        )

    repository = PostgresDashboardRepository(engine)
    health = [row for row in repository.list_feed_health(now) if row["source"] == source]

    assert len(health) == 1
    assert health[0]["stream"] == stream
    assert health[0]["status"] == "connected"
    assert health[0]["age_seconds"] == 3.0
    assert health[0]["details"] == {"derived_from": "market_state_1s"}

    with engine.begin() as connection:
        connection.execute(
            delete(schema.market_state_1s).where(schema.market_state_1s.c.source == source)
        )
