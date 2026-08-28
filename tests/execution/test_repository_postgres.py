from __future__ import annotations

import importlib
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, delete

from bp_engine.live_prediction.models import LivePrediction
from bp_engine.live_prediction.repository import LivePredictionRepository
from bp_engine.storage import schema

DATABASE_URL = os.getenv("BP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BP_TEST_DATABASE_URL is required for PostgreSQL integration coverage",
)


def _prediction() -> LivePrediction:
    start = datetime(2026, 8, 28, 16, 20, tzinfo=UTC)
    scheduled = start + timedelta(minutes=4)
    recorded = scheduled + timedelta(milliseconds=100)
    return LivePrediction(
        prediction_id="d" * 64,
        semantic_sha256="1" * 64,
        prediction_version="live-prediction-v1",
        live_input_version="phase10-live-market-input-v1",
        condition_id="phase12-paper-ledger-postgres",
        slug="btc-updown-5m-phase12-paper-ledger-postgres",
        horizon_seconds=300,
        market_start_at=start,
        market_end_at=start + timedelta(minutes=5),
        scheduled_at=scheduled,
        recorded_at=recorded,
        lateness_ms=100,
        up_token_id="phase12-up-token",
        down_token_id="phase12-down-token",
        source_calibration_run_id="phase9-paper",
        source_calibration_semantic_sha256="2" * 64,
        source_backtest_run_id="phase8-paper",
        source_backtest_semantic_sha256="3" * 64,
        source_training_run_id="phase7-paper",
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
        edge_policy="selected-ask-edge-v1",
        min_edge=0.02,
        training_prior=0.50,
        raw_probability=0.75,
        calibrated_probability=0.75,
        predicted_target=1,
        predicted_side="up",
        market_probability_observed=True,
        market_probability=0.60,
        market_probability_observed_at=scheduled,
        market_probability_downloaded_at=scheduled,
        market_probability_source="polymarket_clob",
        market_probability_dataset="prices_history",
        market_probability_request_params={"market": "phase12-up-token"},
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
        trade=True,
        decision_reason="trade",
        selected_ask=0.60,
        selected_bid=0.58,
        selected_spread=0.02,
        fee=0.0168,
        slippage_buffer=0.01,
        raw_edge=0.15,
        cost_adjusted_edge=0.1232,
        decision_min_edge=0.02,
        edge_decision={"side": "up", "trade": True, "reason": "trade"},
        input_fingerprint="9" * 64,
    )


def _records(module, prediction: LivePrediction):
    order_id = "paper-order-ledger-1"
    submitted = prediction.recorded_at
    order = module.PaperOrderRecord(
        paper_order_id=order_id,
        prediction_id=prediction.prediction_id,
        prediction_semantic_sha256=prediction.semantic_sha256,
        execution_version="paper-execution-v1",
        execution_config_sha256="a" * 64,
        condition_id=prediction.condition_id,
        token_id=prediction.up_token_id,
        selected_side="up",
        requested_shares=Decimal("8.000000"),
        target_notional_usd=Decimal("5.00"),
        submitted_at=submitted,
        arrival_at=submitted + timedelta(milliseconds=250),
        expires_at=submitted + timedelta(milliseconds=2250),
        limit_price=Decimal("0.61"),
        signal_selected_ask=Decimal("0.60"),
        signal_fee_rate=Decimal("0.07"),
        signal_slippage_buffer=Decimal("0.01"),
        execution_config={"latency_ms": 250, "order_ttl_ms": 2000},
        semantic_sha256="b" * 64,
        created_at=submitted,
    )
    fill = module.PaperFillRecord(
        paper_order_id=order_id,
        fill_key="arrival-level-0.60",
        fill_at=submitted + timedelta(milliseconds=250),
        shares=Decimal("3.000000"),
        price=Decimal("0.60"),
        gross_cost=Decimal("1.800000"),
        fee=Decimal("0.050400"),
        total_cost=Decimal("1.850400"),
        signal_ask_slippage=Decimal("0.000000"),
        book_anchor_event_id=101,
        book_anchor_dedupe_key="c" * 64,
        book_applied_event_ids=(102,),
        book_applied_dedupe_keys=("d" * 64,),
        replay_cutoff_at=submitted + timedelta(milliseconds=250),
        semantic_sha256="e" * 64,
        created_at=submitted + timedelta(milliseconds=250),
    )
    terminal = module.PaperOrderTerminalEventRecord(
        paper_order_id=order_id,
        terminal_status="EXPIRED",
        remaining_shares=Decimal("5.000000"),
        event_at=submitted + timedelta(milliseconds=2250),
        reason="order_ttl_elapsed",
        semantic_sha256="f" * 64,
        created_at=submitted + timedelta(milliseconds=2250),
    )
    settlement = module.PaperSettlementRecord(
        paper_order_id=order_id,
        label_version="official-outcome-v1",
        official_outcome="Up",
        official_target=1,
        label_source="polymarket_gamma_snapshot",
        label_source_snapshot_sha256="0" * 64,
        label_source_observed_at=prediction.market_end_at + timedelta(seconds=5),
        filled_shares=Decimal("3.000000"),
        total_fill_cost=Decimal("1.850400"),
        total_fees=Decimal("0.050400"),
        payout=Decimal("3.000000"),
        realized_pnl=Decimal("1.149600"),
        settled_at=prediction.market_end_at + timedelta(seconds=10),
        semantic_sha256="1" * 64,
        created_at=prediction.market_end_at + timedelta(seconds=10),
    )
    return order, fill, terminal, settlement


def test_postgres_paper_ledgers_are_idempotent_and_conflict_fail_closed() -> None:
    assert DATABASE_URL is not None
    module = importlib.import_module("bp_engine.execution.repository")
    models = importlib.import_module("bp_engine.execution.models")
    engine = create_engine(DATABASE_URL)
    schema.metadata.create_all(engine)
    repository = module.PaperExecutionRepository()
    prediction = _prediction()
    order, fill, terminal, settlement = _records(models, prediction)

    with engine.begin() as connection:
        connection.execute(
            delete(schema.paper_settlements).where(
                schema.paper_settlements.c.paper_order_id == order.paper_order_id
            )
        )
        connection.execute(
            delete(schema.paper_order_terminal_events).where(
                schema.paper_order_terminal_events.c.paper_order_id == order.paper_order_id
            )
        )
        connection.execute(
            delete(schema.paper_fills).where(
                schema.paper_fills.c.paper_order_id == order.paper_order_id
            )
        )
        connection.execute(
            delete(schema.paper_orders).where(
                schema.paper_orders.c.paper_order_id == order.paper_order_id
            )
        )
        connection.execute(
            delete(schema.live_predictions).where(
                schema.live_predictions.c.prediction_id == prediction.prediction_id
            )
        )
        LivePredictionRepository().store(connection, prediction)

        order_first = repository.insert_order(connection, order)
        order_second = repository.insert_order(connection, order)
        fill_first = repository.insert_fill(connection, fill)
        fill_second = repository.insert_fill(connection, fill)
        terminal_first = repository.insert_terminal_event(connection, terminal)
        terminal_second = repository.insert_terminal_event(connection, terminal)
        settlement_first = repository.insert_settlement(connection, settlement)
        settlement_second = repository.insert_settlement(connection, settlement)

        with pytest.raises(module.PaperLedgerConflictError):
            repository.insert_order(
                connection,
                replace(order, semantic_sha256="2" * 64),
            )
        with pytest.raises(module.PaperLedgerConflictError):
            repository.insert_fill(
                connection,
                replace(fill, semantic_sha256="3" * 64),
            )
        with pytest.raises(module.PaperLedgerConflictError):
            repository.insert_terminal_event(
                connection,
                replace(terminal, semantic_sha256="4" * 64),
            )
        with pytest.raises(module.PaperLedgerConflictError):
            repository.insert_settlement(
                connection,
                replace(settlement, semantic_sha256="5" * 64),
            )

        connection.execute(
            delete(schema.paper_settlements).where(
                schema.paper_settlements.c.paper_order_id == order.paper_order_id
            )
        )
        connection.execute(
            delete(schema.paper_order_terminal_events).where(
                schema.paper_order_terminal_events.c.paper_order_id == order.paper_order_id
            )
        )
        connection.execute(
            delete(schema.paper_fills).where(
                schema.paper_fills.c.paper_order_id == order.paper_order_id
            )
        )
        connection.execute(
            delete(schema.paper_orders).where(
                schema.paper_orders.c.paper_order_id == order.paper_order_id
            )
        )
        connection.execute(
            delete(schema.live_predictions).where(
                schema.live_predictions.c.prediction_id == prediction.prediction_id
            )
        )

    assert order_first.created is True
    assert order_second.created is False
    assert fill_first.created is True
    assert fill_second.created is False
    assert terminal_first.created is True
    assert terminal_second.created is False
    assert settlement_first.created is True
    assert settlement_second.created is False
