from __future__ import annotations

import importlib
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, insert, text
from sqlalchemy.exc import DBAPIError

from bp_engine.storage.schema import (
    feed_incidents,
    feed_status,
    live_prediction_evaluations,
    live_predictions,
    market_state_1s,
    metadata,
    polymarket_markets,
)

queries_module = importlib.import_module("bp_engine.dashboard.queries")
DashboardQueries = queries_module.DashboardQueries


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    return engine


def _market_values(now: datetime, *, index: int, horizon: int) -> dict[str, object]:
    return {
        "gamma_market_id": f"gamma-{index}",
        "event_id": f"event-{index}",
        "condition_id": f"condition-{index}",
        "slug": f"btc-{horizon}-{index}",
        "question": f"BTC up {horizon} {index}?",
        "horizon_seconds": horizon,
        "start_at": now - timedelta(minutes=1),
        "end_at": now + timedelta(minutes=10),
        "up_token_id": f"up-{index}",
        "down_token_id": f"down-{index}",
        "resolution_source": "rules",
        "rules_text": "rules",
        "rules_hash": f"rules-{index}",
        "active": True,
        "closed": False,
        "accepting_orders": True,
        "resolved_outcome": None,
        "discovered_at": now - timedelta(minutes=2),
        "updated_at": now,
    }


def _prediction_values(now: datetime, *, index: int, horizon: int = 300) -> dict[str, object]:
    start = now - timedelta(minutes=5)
    end = now + timedelta(minutes=5)
    scheduled = now - timedelta(seconds=2)
    digest = "a" * 64
    return {
        "prediction_id": f"prediction-{index}",
        "semantic_sha256": digest,
        "prediction_version": "live-prediction-v1",
        "live_input_version": "phase10-live-market-input-v1",
        "condition_id": f"condition-{index}",
        "slug": f"btc-{horizon}-{index}",
        "horizon_seconds": horizon,
        "market_start_at": start,
        "market_end_at": end,
        "scheduled_at": scheduled,
        "recorded_at": now - timedelta(seconds=1),
        "lateness_ms": 1000,
        "up_token_id": f"up-{index}",
        "down_token_id": f"down-{index}",
        "source_calibration_run_id": "calibration-run",
        "source_calibration_semantic_sha256": digest,
        "source_backtest_run_id": "backtest-run",
        "source_backtest_semantic_sha256": digest,
        "source_training_run_id": "training-run",
        "source_training_semantic_sha256": digest,
        "calibration_version": "platt-or-identity-v1",
        "edge_policy_version": "selected-ask-edge-v1",
        "source_feature_version": "core-v1",
        "source_label_version": "official-outcome-v1",
        "selected_offset_seconds": 120,
        "policy_sha256": digest,
        "calibration_fit": {"kind": "identity"},
        "calibration_fit_sha256": digest,
        "edge_config": {"fee": "0.01"},
        "edge_config_sha256": digest,
        "edge_policy": "trade_threshold",
        "min_edge": Decimal("0.020000000000000000"),
        "training_prior": Decimal("0.500000000000000000"),
        "raw_probability": Decimal("0.610000000000000000"),
        "calibrated_probability": Decimal("0.620000000000000000"),
        "predicted_target": 1,
        "predicted_side": "Up",
        "market_probability_observed": True,
        "market_probability": Decimal("0.610000000000000000"),
        "market_probability_observed_at": scheduled,
        "market_probability_downloaded_at": scheduled,
        "market_probability_source": "polymarket_clob",
        "market_probability_dataset": "prices_history",
        "market_probability_request_params": {"fidelity": "1"},
        "market_probability_response_sha256": digest,
        "up_best_bid": Decimal("0.580000000000000000"),
        "up_best_ask": Decimal("0.600000000000000000"),
        "up_book_cutoff_at": scheduled,
        "up_book_fresh": True,
        "down_best_bid": Decimal("0.390000000000000000"),
        "down_best_ask": Decimal("0.410000000000000000"),
        "down_book_cutoff_at": scheduled,
        "down_book_fresh": True,
        "selected_side": "Up",
        "executable": True,
        "trade": True,
        "decision_reason": "edge_threshold_met",
        "selected_ask": Decimal("0.600000000000000000"),
        "selected_bid": Decimal("0.580000000000000000"),
        "selected_spread": Decimal("0.020000000000000000"),
        "fee": Decimal("0.010000000000000000"),
        "slippage_buffer": Decimal("0.005000000000000000"),
        "raw_edge": Decimal("0.020000000000000000"),
        "cost_adjusted_edge": Decimal("0.005000000000000000"),
        "decision_min_edge": Decimal("0.020000000000000000"),
        "edge_decision": {"trade": True},
        "input_fingerprint": digest,
    }


def _evaluation_values(now: datetime, *, index: int) -> dict[str, object]:
    return {
        "prediction_id": f"prediction-{index}",
        "label_version": "official-outcome-v1",
        "official_outcome": "Up",
        "official_target": 1,
        "label_source": "polymarket_gamma",
        "label_source_snapshot_sha256": "b" * 64,
        "label_source_observed_at": now + timedelta(minutes=6),
        "evaluated_at": now + timedelta(minutes=7),
        "correct": True,
        "raw_log_loss": Decimal("0.49"),
        "raw_brier": Decimal("0.1521"),
        "calibrated_log_loss": Decimal("0.47"),
        "calibrated_brier": Decimal("0.1444"),
        "hypothetical_gross_pnl": Decimal("0.40"),
        "hypothetical_assumed_cost_pnl": Decimal("0.38"),
        "semantic_sha256": "c" * 64,
    }


def test_active_markets_use_verified_horizons_latest_exact_books_and_stored_prediction() -> None:
    engine = _engine()
    queries = DashboardQueries(engine)
    now = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)

    with engine.begin() as connection:
        for index, horizon in enumerate((300, 900, 600), start=1):
            connection.execute(insert(polymarket_markets).values(**_market_values(now, index=index, horizon=horizon)))
        connection.execute(
            insert(market_state_1s),
            [
                {
                    "bucket_at": now - timedelta(seconds=3),
                    "state_key": "old-up",
                    "source": "polymarket",
                    "stream": "market",
                    "instrument": "condition-1",
                    "market_id": "condition-1",
                    "asset_id": "up-1",
                    "last_event_at": now - timedelta(seconds=3),
                    "state": {"best_bid": "0.10", "best_ask": "0.20"},
                },
                {
                    "bucket_at": now - timedelta(seconds=1),
                    "state_key": "new-up",
                    "source": "polymarket",
                    "stream": "market",
                    "instrument": "condition-1",
                    "market_id": "condition-1",
                    "asset_id": "up-1",
                    "last_event_at": now - timedelta(seconds=1),
                    "state": {"best_bid": "0.70", "best_ask": "0.71"},
                },
                {
                    "bucket_at": now - timedelta(seconds=1),
                    "state_key": "new-down",
                    "source": "polymarket",
                    "stream": "market",
                    "instrument": "condition-1",
                    "market_id": "condition-1",
                    "asset_id": "down-1",
                    "last_event_at": now - timedelta(seconds=1),
                    "state": {"best_bid": "0.29", "best_ask": "0.30"},
                },
            ],
        )
        connection.execute(insert(live_predictions).values(**_prediction_values(now, index=1)))

    rows = queries.active_markets(now=now, horizon_seconds=None, limit=10)

    assert [row["horizon_seconds"] for row in rows] == [300, 900]
    first = rows[0]
    assert first["current_up_best_bid"] == "0.70"
    assert first["current_up_best_ask"] == "0.71"
    assert first["current_down_best_bid"] == "0.29"
    assert first["prediction"]["up_best_bid"] == Decimal("0.580000000000000000")
    assert first["prediction"]["calibrated_probability"] == Decimal("0.620000000000000000")
    assert first["prediction"]["cost_adjusted_edge"] == Decimal("0.005000000000000000")


def test_active_markets_reject_unverified_horizon_and_unbounded_limit() -> None:
    queries = DashboardQueries(_engine())
    now = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="horizon"):
        queries.active_markets(now=now, horizon_seconds=600, limit=10)
    with pytest.raises(ValueError, match="limit"):
        queries.active_markets(now=now, horizon_seconds=None, limit=0)
    with pytest.raises(ValueError, match="limit"):
        queries.active_markets(now=now, horizon_seconds=None, limit=101)


def test_prediction_history_attaches_only_evaluation_child_and_preserves_decimals() -> None:
    engine = _engine()
    queries = DashboardQueries(engine)
    now = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)

    with engine.begin() as connection:
        unresolved = _market_values(now, index=1, horizon=300)
        unresolved["resolved_outcome"] = "Down"
        connection.execute(insert(polymarket_markets).values(**unresolved))
        connection.execute(insert(live_predictions).values(**_prediction_values(now, index=1)))

    pending = queries.predictions(evaluation_state="pending", limit=10)
    assert len(pending) == 1
    assert pending[0]["evaluation"] is None
    assert pending[0]["calibrated_probability"] == Decimal("0.620000000000000000")

    with engine.begin() as connection:
        connection.execute(insert(live_prediction_evaluations).values(**_evaluation_values(now, index=1)))

    evaluated = queries.predictions(evaluation_state="evaluated", limit=10)
    assert len(evaluated) == 1
    assert evaluated[0]["evaluation"]["official_outcome"] == "Up"
    assert evaluated[0]["evaluation"]["calibrated_brier"] == Decimal("0.144400000000000000")
    assert queries.predictions(evaluation_state="pending", limit=10) == []


def test_feed_health_aggregates_only_incidents_inside_bounded_window() -> None:
    engine = _engine()
    queries = DashboardQueries(engine)
    now = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)

    with engine.begin() as connection:
        connection.execute(
            insert(feed_status).values(
                source="polymarket",
                stream="market",
                status="healthy",
                last_received_at=now - timedelta(seconds=2),
                last_source_timestamp=now - timedelta(seconds=3),
                updated_at=now - timedelta(seconds=1),
                details={},
            )
        )
        connection.execute(
            insert(feed_incidents),
            [
                {
                    "source": "polymarket",
                    "stream": "market",
                    "incident_type": "stale",
                    "observed_at": now - timedelta(minutes=2),
                    "details": {},
                },
                {
                    "source": "polymarket",
                    "stream": "market",
                    "incident_type": "old",
                    "observed_at": now - timedelta(hours=2),
                    "details": {},
                },
            ],
        )

    rows = queries.feed_health(now=now, incident_window=timedelta(hours=1))

    assert rows[0]["recent_incident_count"] == 1
    assert rows[0]["most_recent_incident_type"] == "stale"
    assert rows[0]["last_received_at"] == now - timedelta(seconds=2)


def test_evaluation_rows_join_prediction_probability_and_horizon() -> None:
    engine = _engine()
    queries = DashboardQueries(engine)
    now = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)

    with engine.begin() as connection:
        connection.execute(insert(live_predictions).values(**_prediction_values(now, index=1, horizon=900)))
        connection.execute(insert(live_prediction_evaluations).values(**_evaluation_values(now, index=1)))

    rows = queries.evaluation_rows(limit=100)

    assert rows[0]["horizon_seconds"] == 900
    assert rows[0]["calibrated_probability"] == Decimal("0.620000000000000000")
    assert rows[0]["official_target"] == 1


def test_postgres_dashboard_transaction_rejects_write() -> None:
    database_url = os.getenv("BP_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("BP_TEST_DATABASE_URL is not configured")
    queries = DashboardQueries(create_engine(database_url))

    with pytest.raises(DBAPIError):
        with queries.read_only_connection() as connection:
            connection.execute(text("CREATE TEMP TABLE dashboard_write_probe(id integer)"))
