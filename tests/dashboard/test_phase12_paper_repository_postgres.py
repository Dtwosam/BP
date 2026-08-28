from __future__ import annotations

from dataclasses import asdict, replace
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, delete

from bp_engine.dashboard.repository import PostgresDashboardRepository
from bp_engine.execution.repository import PaperExecutionRepository
from bp_engine.live_prediction.repository import LivePredictionRepository
from bp_engine.storage import schema
from tests.execution.test_repository_postgres import DATABASE_URL, _prediction, _records

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BP_TEST_DATABASE_URL is required for PostgreSQL integration coverage",
)


def _cleanup(connection, prediction_ids: tuple[str, ...]) -> None:
    connection.execute(delete(schema.paper_settlements))
    connection.execute(delete(schema.paper_order_terminal_events))
    connection.execute(delete(schema.paper_fills))
    connection.execute(delete(schema.paper_orders))
    connection.execute(
        delete(schema.live_predictions).where(
            schema.live_predictions.c.prediction_id.in_(prediction_ids)
        )
    )


def test_postgres_dashboard_reports_paper_account_and_reconciliation() -> None:
    assert DATABASE_URL is not None
    engine = create_engine(DATABASE_URL)
    schema.metadata.create_all(engine)
    prediction_repository = LivePredictionRepository()
    paper_repository = PaperExecutionRepository()

    trade_prediction = _prediction()
    no_trade_prediction = replace(
        trade_prediction,
        prediction_id="e" * 64,
        semantic_sha256="2" * 64,
        condition_id="phase12-dashboard-no-trade",
        slug="btc-updown-5m-phase12-dashboard-no-trade",
        trade=False,
        decision_reason="no_trade_edge",
        edge_decision={"side": "up", "trade": False, "reason": "no_trade_edge"},
    )
    prediction_ids = (trade_prediction.prediction_id, no_trade_prediction.prediction_id)
    order, fill, terminal, settlement = _records(
        __import__("bp_engine.execution.models", fromlist=["PaperOrderRecord"]),
        trade_prediction,
    )

    try:
        with engine.begin() as connection:
            _cleanup(connection, prediction_ids)
            prediction_repository.store(connection, trade_prediction)
            prediction_repository.store(connection, no_trade_prediction)
            paper_repository.insert_order(connection, order)
            paper_repository.insert_fill(connection, fill)
            paper_repository.insert_terminal_event(connection, terminal)
            paper_repository.insert_settlement(connection, settlement)

        evidence = PostgresDashboardRepository(engine).get_paper_execution_evidence(
            history_limit=10
        )

        pnl = evidence["paper_pnl"]
        assert pnl["status"] == "AVAILABLE"
        assert Decimal(str(pnl["starting_cash"])) == Decimal("100")
        assert Decimal(str(pnl["current_cash"])) == Decimal("101.149600")
        assert Decimal(str(pnl["realized_pnl"])) == Decimal("1.149600")
        assert Decimal(str(pnl["open_capital"])) == Decimal("0")
        assert pnl["unrealized_value"] is None
        assert pnl["settled_trade_count"] == 1
        assert pnl["open_position_count"] == 0
        assert pnl["fill_count"] == 1
        assert pnl["no_fill_expired_count"] == 0
        assert Decimal(str(pnl["total_fees"])) == Decimal("0.050400")
        assert Decimal(str(pnl["total_slippage_cost"])) == Decimal("0")
        assert Decimal(str(pnl["max_realized_equity_drawdown"])) == Decimal("0")
        assert Decimal(str(pnl["return_on_starting_cash"])) == Decimal("0.011496")

        reconciliation = pnl["reconciliation"]
        assert reconciliation["status"] == "OK"
        assert reconciliation["violation_count"] == 0
        assert reconciliation["paper_order_count"] == 1
        assert reconciliation["trade_signal_count"] >= 1
        assert reconciliation["no_trade_signal_count"] >= 1

        assert [row["paper_order_id"] for row in evidence["paper_orders"]] == [
            order.paper_order_id
        ]
        assert [row["paper_order_id"] for row in evidence["paper_fills"]] == [
            order.paper_order_id
        ]
        assert [row["paper_order_id"] for row in evidence["paper_settlements"]] == [
            order.paper_order_id
        ]
    finally:
        with engine.begin() as connection:
            _cleanup(connection, prediction_ids)


def test_reconciliation_flags_order_created_from_immutable_no_trade_signal() -> None:
    assert DATABASE_URL is not None
    engine = create_engine(DATABASE_URL)
    schema.metadata.create_all(engine)
    prediction_repository = LivePredictionRepository()

    source = _prediction()
    no_trade_prediction = replace(
        source,
        prediction_id="a" * 64,
        semantic_sha256="b" * 64,
        condition_id="phase12-dashboard-invalid-no-trade",
        slug="btc-updown-5m-phase12-dashboard-invalid-no-trade",
        trade=False,
        decision_reason="edge_below_threshold",
        edge_decision={"side": "up", "trade": False, "reason": "skip"},
    )
    base_order, _, _, _ = _records(
        __import__("bp_engine.execution.models", fromlist=["PaperOrderRecord"]),
        source,
    )
    invalid_order = replace(
        base_order,
        paper_order_id="paper-order-from-no-trade",
        prediction_id=no_trade_prediction.prediction_id,
        prediction_semantic_sha256=no_trade_prediction.semantic_sha256,
        condition_id=no_trade_prediction.condition_id,
        token_id=no_trade_prediction.up_token_id,
        semantic_sha256="c" * 64,
    )
    prediction_ids = (no_trade_prediction.prediction_id,)

    try:
        with engine.begin() as connection:
            _cleanup(connection, prediction_ids)
            prediction_repository.store(connection, no_trade_prediction)
            # Deliberately bypass PaperExecutionRepository's eligibility guard to model
            # corrupted/foreign ledger state. Normal application code cannot insert this.
            connection.execute(schema.paper_orders.insert().values(**asdict(invalid_order)))

        reconciliation = PostgresDashboardRepository(engine).get_paper_execution_evidence()[
            "paper_pnl"
        ]["reconciliation"]

        assert reconciliation["status"] == "VIOLATION"
        assert reconciliation["violation_count"] == 1
        assert reconciliation["violations"]["ineligible_source_signal"] == 1
        assert reconciliation["paper_order_count"] == 1
        assert reconciliation["no_trade_signal_count"] >= 1
    finally:
        with engine.begin() as connection:
            _cleanup(connection, prediction_ids)
