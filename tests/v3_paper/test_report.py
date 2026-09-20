from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import create_engine, insert

from bp_engine.live_prediction.repository import (
    LivePredictionEvaluationRepository,
    LivePredictionRepository,
)
from bp_engine.storage import schema
from bp_engine.v3_paper.report import build_v3_paper_report
from bp_engine.v3_paper.service import (
    V3_PAPER_EXECUTION_VERSION,
    V3_PAPER_PREDICTION_VERSION,
)
from tests.execution.test_service_postgres import BASE, _evaluation, _prediction


def _engine():
    engine = create_engine("sqlite://")
    schema.metadata.create_all(engine)
    return engine


def _store_prediction(
    connection,
    *,
    prediction_id: str,
    semantic_sha256: str,
    condition_id: str,
    version: str,
) -> None:
    prediction = replace(
        _prediction(
            prediction_id=prediction_id,
            semantic_sha256=semantic_sha256,
            condition_id=condition_id,
            trade=True,
        ),
        prediction_version=version,
    )
    LivePredictionRepository().store(connection, prediction)


def _insert_order(
    connection,
    *,
    paper_order_id: str,
    prediction_id: str,
    prediction_semantic_sha256: str,
    condition_id: str,
    execution_version: str,
    selected_side: str = "up",
) -> None:
    connection.execute(
        insert(schema.paper_orders).values(
            paper_order_id=paper_order_id,
            prediction_id=prediction_id,
            prediction_semantic_sha256=prediction_semantic_sha256,
            execution_version=execution_version,
            execution_config_sha256="e" * 64,
            condition_id=condition_id,
            token_id=f"{condition_id}-{selected_side}",
            selected_side=selected_side,
            requested_shares=Decimal("10"),
            target_notional_usd=Decimal("5"),
            submitted_at=BASE,
            arrival_at=BASE + timedelta(milliseconds=250),
            expires_at=BASE + timedelta(seconds=2, milliseconds=250),
            limit_price=Decimal("0.41"),
            signal_selected_ask=Decimal("0.40"),
            signal_fee_rate=Decimal("0.07"),
            signal_slippage_buffer=Decimal("0.01"),
            execution_config={
                "execution_version": execution_version,
                "starting_cash_usd": "100.00",
                "target_notional_usd": "5.00",
                "latency_ms": 250,
                "order_ttl_ms": 2000,
                "share_precision": 6,
            },
            semantic_sha256="f" * 64,
            created_at=BASE,
        )
    )


def _insert_fill(
    connection,
    *,
    paper_order_id: str,
    fill_key: str,
    cost: Decimal,
) -> None:
    shares = Decimal("10")
    price = Decimal("0.40")
    fee = cost - (shares * price)
    connection.execute(
        insert(schema.paper_fills).values(
            paper_order_id=paper_order_id,
            fill_key=fill_key,
            fill_at=BASE + timedelta(milliseconds=300),
            shares=shares,
            price=price,
            gross_cost=shares * price,
            fee=fee,
            total_cost=cost,
            signal_ask_slippage=Decimal("0"),
            book_anchor_event_id=1,
            book_anchor_dedupe_key="sha256:" + "1" * 64,
            book_applied_event_ids=[],
            book_applied_dedupe_keys=[],
            replay_cutoff_at=BASE + timedelta(milliseconds=300),
            semantic_sha256="1" * 64,
            created_at=BASE + timedelta(milliseconds=300),
        )
    )


def _insert_terminal(connection, *, paper_order_id: str) -> None:
    connection.execute(
        insert(schema.paper_order_terminal_events).values(
            paper_order_id=paper_order_id,
            terminal_status="FILLED",
            remaining_shares=Decimal("0"),
            event_at=BASE + timedelta(seconds=2, milliseconds=250),
            reason="filled",
            semantic_sha256="2" * 64,
            created_at=BASE + timedelta(seconds=2, milliseconds=250),
        )
    )


def _insert_settlement(
    connection,
    *,
    paper_order_id: str,
    fill_cost: Decimal,
    payout: Decimal,
) -> None:
    connection.execute(
        insert(schema.paper_settlements).values(
            paper_order_id=paper_order_id,
            label_version="official-outcome-v1",
            official_outcome="Up",
            official_target=1,
            label_source="polymarket_gamma_snapshot",
            label_source_snapshot_sha256="3" * 64,
            label_source_observed_at=BASE + timedelta(minutes=1, seconds=1),
            filled_shares=Decimal("10"),
            total_fill_cost=fill_cost,
            total_fees=fill_cost - Decimal("4"),
            payout=payout,
            realized_pnl=payout - fill_cost,
            settled_at=BASE + timedelta(minutes=1, seconds=2),
            semantic_sha256="4" * 64,
            created_at=BASE + timedelta(minutes=1, seconds=2),
        )
    )


def test_v3_report_excludes_legacy_rows_and_separates_signal_from_execution() -> None:
    engine = _engine()
    v3_prediction_id = "a" * 64
    v3_semantic = "b" * 64
    legacy_prediction_id = "c" * 64
    legacy_semantic = "d" * 64

    with engine.begin() as connection:
        _store_prediction(
            connection,
            prediction_id=v3_prediction_id,
            semantic_sha256=v3_semantic,
            condition_id="v3-report",
            version=V3_PAPER_PREDICTION_VERSION,
        )
        _store_prediction(
            connection,
            prediction_id=legacy_prediction_id,
            semantic_sha256=legacy_semantic,
            condition_id="legacy-report",
            version="live-prediction-v1",
        )
        evaluation = replace(
            _evaluation(),
            prediction_id=v3_prediction_id,
            semantic_sha256="5" * 64,
        )
        LivePredictionEvaluationRepository().store(connection, evaluation)

        _insert_order(
            connection,
            paper_order_id="v3-order",
            prediction_id=v3_prediction_id,
            prediction_semantic_sha256=v3_semantic,
            condition_id="v3-report",
            execution_version=V3_PAPER_EXECUTION_VERSION,
        )
        _insert_fill(
            connection,
            paper_order_id="v3-order",
            fill_key="v3-fill",
            cost=Decimal("4.20"),
        )
        _insert_terminal(connection, paper_order_id="v3-order")
        _insert_settlement(
            connection,
            paper_order_id="v3-order",
            fill_cost=Decimal("4.20"),
            payout=Decimal("10"),
        )

        _insert_order(
            connection,
            paper_order_id="legacy-order",
            prediction_id=legacy_prediction_id,
            prediction_semantic_sha256=legacy_semantic,
            condition_id="legacy-report",
            execution_version="paper-execution-v1",
        )
        _insert_fill(
            connection,
            paper_order_id="legacy-order",
            fill_key="legacy-fill",
            cost=Decimal("90"),
        )
        _insert_terminal(connection, paper_order_id="legacy-order")
        _insert_settlement(
            connection,
            paper_order_id="legacy-order",
            fill_cost=Decimal("90"),
            payout=Decimal("0"),
        )

    report = build_v3_paper_report(engine)
    summary = report["summary"]

    assert report["prediction_version"] == V3_PAPER_PREDICTION_VERSION
    assert report["execution_version"] == V3_PAPER_EXECUTION_VERSION
    assert summary["prediction_count"] == 1
    assert summary["trade_signal_count"] == 1
    assert summary["evaluated_trade_signal_count"] == 1
    assert summary["correct_trade_signal_count"] == 1
    assert summary["trade_signal_accuracy"] == 1.0
    assert summary["paper_order_count"] == 1
    assert summary["paper_fill_count"] == 1
    assert summary["settled_order_count"] == 1
    assert summary["wins"] == 1
    assert summary["losses"] == 0
    assert summary["realized_pnl"] == Decimal("5.80")
    assert summary["settled_fill_cost"] == Decimal("4.20")
    assert summary["virtual_current_cash"] == Decimal("105.80")
    assert summary["open_fill_cost"] == Decimal("0")
    assert report["by_side"]["up"]["realized_pnl"] == Decimal("5.80")
    assert report["integrity"]["invalid_order_source_count"] == 0
    assert len(report["recent_settled_trades"]) == 1
    assert report["recent_settled_trades"][0]["condition_id"] == "v3-report"


def test_v3_report_counts_open_v3_fill_without_treating_it_as_realized_pnl() -> None:
    engine = _engine()
    prediction_id = "6" * 64
    semantic_sha256 = "7" * 64

    with engine.begin() as connection:
        _store_prediction(
            connection,
            prediction_id=prediction_id,
            semantic_sha256=semantic_sha256,
            condition_id="v3-open",
            version=V3_PAPER_PREDICTION_VERSION,
        )
        _insert_order(
            connection,
            paper_order_id="v3-open-order",
            prediction_id=prediction_id,
            prediction_semantic_sha256=semantic_sha256,
            condition_id="v3-open",
            execution_version=V3_PAPER_EXECUTION_VERSION,
        )
        _insert_fill(
            connection,
            paper_order_id="v3-open-order",
            fill_key="v3-open-fill",
            cost=Decimal("4.20"),
        )
        _insert_terminal(connection, paper_order_id="v3-open-order")

    report = build_v3_paper_report(engine)
    summary = report["summary"]

    assert summary["filled_order_count"] == 1
    assert summary["settled_order_count"] == 0
    assert summary["open_filled_order_count"] == 1
    assert summary["realized_pnl"] == Decimal("0")
    assert summary["open_fill_cost"] == Decimal("4.20")
    assert summary["virtual_current_cash"] == Decimal("95.80")
