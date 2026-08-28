from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, delete, select

from bp_engine.execution.models import PaperExecutionConfig
from bp_engine.execution.service import PaperExecutionService
from bp_engine.live_prediction.models import LivePrediction, LivePredictionEvaluation
from bp_engine.live_prediction.repository import (
    LivePredictionEvaluationRepository,
    LivePredictionRepository,
)
from bp_engine.recorder.models import RawEvent
from bp_engine.storage import schema
from bp_engine.storage.recorder import RecorderRepository

DATABASE_URL = os.getenv("BP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BP_TEST_DATABASE_URL is required for PostgreSQL integration coverage",
)

BASE = datetime(2026, 8, 28, 18, 0, tzinfo=UTC)
TRADE_PREDICTION_ID = "1" * 64
SKIP_PREDICTION_ID = "2" * 64
TRADE_CONDITION = "phase12-service-trade"
SKIP_CONDITION = "phase12-service-skip"


def _prediction(
    *,
    prediction_id: str,
    semantic_sha256: str,
    condition_id: str,
    trade: bool,
) -> LivePrediction:
    market_start = BASE - timedelta(minutes=4)
    market_end = BASE + timedelta(minutes=1)
    return LivePrediction(
        prediction_id=prediction_id,
        semantic_sha256=semantic_sha256,
        prediction_version="live-prediction-v1",
        live_input_version="phase10-live-market-input-v1",
        condition_id=condition_id,
        slug=f"btc-updown-5m-{condition_id}",
        horizon_seconds=300,
        market_start_at=market_start,
        market_end_at=market_end,
        scheduled_at=BASE,
        recorded_at=BASE,
        lateness_ms=0,
        up_token_id=f"{condition_id}-up",
        down_token_id=f"{condition_id}-down",
        source_calibration_run_id="phase9-source",
        source_calibration_semantic_sha256="3" * 64,
        source_backtest_run_id="phase8-source",
        source_backtest_semantic_sha256="4" * 64,
        source_training_run_id="phase7-source",
        source_training_semantic_sha256="5" * 64,
        calibration_version="platt-or-identity-v1",
        edge_policy_version="selected-ask-edge-v1",
        source_feature_version="core-v1",
        source_label_version="official-outcome-v1",
        selected_offset_seconds=240,
        policy_sha256="6" * 64,
        calibration_fit={"method": "identity", "intercept": None, "coefficient": None},
        calibration_fit_sha256="7" * 64,
        edge_config={
            "fee_rate": 0.07,
            "slippage_buffer": 0.01,
            "min_edge_grid": [0.0, 0.02],
            "min_validation_trades": 3,
            "max_spread": None,
        },
        edge_config_sha256="8" * 64,
        edge_policy="trade_threshold",
        min_edge=0.02,
        training_prior=0.48,
        raw_probability=0.72,
        calibrated_probability=0.72,
        predicted_target=1,
        predicted_side="up",
        market_probability_observed=True,
        market_probability=0.60,
        market_probability_observed_at=BASE,
        market_probability_downloaded_at=BASE,
        market_probability_source="polymarket_clob",
        market_probability_dataset="prices_history",
        market_probability_request_params={"market": f"{condition_id}-up", "fidelity": "1"},
        market_probability_response_sha256="9" * 64,
        up_best_bid=0.59,
        up_best_ask=0.60,
        up_book_cutoff_at=BASE,
        up_book_fresh=True,
        down_best_bid=0.39,
        down_best_ask=0.41,
        down_book_cutoff_at=BASE,
        down_book_fresh=True,
        selected_side="up",
        executable=True,
        trade=trade,
        decision_reason="trade" if trade else "edge_below_threshold",
        selected_ask=0.60,
        selected_bid=0.59,
        selected_spread=0.01,
        fee=0.0168,
        slippage_buffer=0.01,
        raw_edge=0.12,
        cost_adjusted_edge=0.0932,
        decision_min_edge=0.02,
        edge_decision={"side": "up", "trade": trade, "reason": "trade" if trade else "skip"},
        input_fingerprint="a" * 64,
    )


def _raw_event(
    *,
    event_type: str,
    received_at: datetime,
    payload: dict[str, object],
    asset_id: str | None,
) -> RawEvent:
    return RawEvent.build(
        source="polymarket",
        stream="market",
        instrument=TRADE_CONDITION,
        event_type=event_type,
        source_timestamp=None,
        received_at=received_at,
        market_id=TRADE_CONDITION,
        asset_id=asset_id,
        payload=payload,
    )


def _evaluation() -> LivePredictionEvaluation:
    return LivePredictionEvaluation(
        prediction_id=TRADE_PREDICTION_ID,
        label_version="official-outcome-v1",
        official_outcome="Up",
        official_target=1,
        label_source="polymarket_gamma_snapshot",
        label_source_snapshot_sha256="b" * 64,
        label_source_observed_at=BASE + timedelta(minutes=1, seconds=1),
        evaluated_at=BASE + timedelta(minutes=1, seconds=2),
        correct=True,
        raw_log_loss=0.328504,
        raw_brier=0.0784,
        calibrated_log_loss=0.328504,
        calibrated_brier=0.0784,
        hypothetical_gross_pnl=0.40,
        hypothetical_assumed_cost_pnl=0.36,
        semantic_sha256="c" * 64,
    )


def _cleanup(connection, *, raw_keys: tuple[str, ...]) -> None:
    order_ids = tuple(
        connection.execute(
            select(schema.paper_orders.c.paper_order_id).where(
                schema.paper_orders.c.prediction_id.in_(
                    (TRADE_PREDICTION_ID, SKIP_PREDICTION_ID)
                )
            )
        ).scalars()
    )
    if order_ids:
        connection.execute(
            delete(schema.paper_settlements).where(
                schema.paper_settlements.c.paper_order_id.in_(order_ids)
            )
        )
        connection.execute(
            delete(schema.paper_fills).where(schema.paper_fills.c.paper_order_id.in_(order_ids))
        )
        connection.execute(
            delete(schema.paper_order_terminal_events).where(
                schema.paper_order_terminal_events.c.paper_order_id.in_(order_ids)
            )
        )
        connection.execute(
            delete(schema.paper_orders).where(schema.paper_orders.c.paper_order_id.in_(order_ids))
        )
    connection.execute(
        delete(schema.live_prediction_evaluations).where(
            schema.live_prediction_evaluations.c.prediction_id.in_(
                (TRADE_PREDICTION_ID, SKIP_PREDICTION_ID)
            )
        )
    )
    connection.execute(
        delete(schema.live_predictions).where(
            schema.live_predictions.c.prediction_id.in_(
                (TRADE_PREDICTION_ID, SKIP_PREDICTION_ID)
            )
        )
    )
    if raw_keys:
        connection.execute(
            delete(schema.raw_market_events).where(schema.raw_market_events.c.dedupe_key.in_(raw_keys))
        )


def test_paper_service_is_causal_idempotent_and_settles_from_official_evaluation() -> None:
    assert DATABASE_URL is not None
    engine = create_engine(DATABASE_URL)
    schema.metadata.create_all(engine)
    prediction_repository = LivePredictionRepository()
    evaluation_repository = LivePredictionEvaluationRepository()
    recorder = RecorderRepository()
    config = PaperExecutionConfig()
    service = PaperExecutionService(engine=engine, config=config)

    trade_prediction = _prediction(
        prediction_id=TRADE_PREDICTION_ID,
        semantic_sha256="d" * 64,
        condition_id=TRADE_CONDITION,
        trade=True,
    )
    skip_prediction = _prediction(
        prediction_id=SKIP_PREDICTION_ID,
        semantic_sha256="e" * 64,
        condition_id=SKIP_CONDITION,
        trade=False,
    )
    anchor = _raw_event(
        event_type="book",
        received_at=BASE + timedelta(milliseconds=200),
        asset_id=f"{TRADE_CONDITION}-up",
        payload={
            "event_type": "book",
            "market": TRADE_CONDITION,
            "asset_id": f"{TRADE_CONDITION}-up",
            "bids": [{"price": "0.59", "size": "20"}],
            "asks": [
                {"price": "0.60", "size": "2"},
                {"price": "0.61", "size": "2"},
                {"price": "0.62", "size": "10"},
            ],
        },
    )
    replenish = _raw_event(
        event_type="price_change",
        received_at=BASE + timedelta(milliseconds=700),
        asset_id=None,
        payload={
            "event_type": "price_change",
            "market": TRADE_CONDITION,
            "price_changes": [
                {
                    "asset_id": f"{TRADE_CONDITION}-up",
                    "side": "SELL",
                    "price": "0.61",
                    "size": "3",
                }
            ],
        },
    )
    raw_events = (anchor, replenish)
    raw_keys = tuple(event.dedupe_key for event in raw_events)

    with engine.begin() as connection:
        _cleanup(connection, raw_keys=raw_keys)
        prediction_repository.store(connection, trade_prediction)
        prediction_repository.store(connection, skip_prediction)
        recorder.insert_events(connection, raw_events)
        predictions_before = tuple(
            dict(row)
            for row in connection.execute(
                select(schema.live_predictions)
                .where(
                    schema.live_predictions.c.prediction_id.in_(
                        (TRADE_PREDICTION_ID, SKIP_PREDICTION_ID)
                    )
                )
                .order_by(schema.live_predictions.c.prediction_id)
            ).mappings()
        )
        raw_before = tuple(
            dict(row)
            for row in connection.execute(
                select(schema.raw_market_events)
                .where(schema.raw_market_events.c.dedupe_key.in_(raw_keys))
                .order_by(schema.raw_market_events.c.id)
            ).mappings()
        )

    first = service.run_once(now=BASE + timedelta(seconds=3))

    with engine.begin() as connection:
        orders = connection.execute(select(schema.paper_orders)).mappings().all()
        service_orders = [
            row
            for row in orders
            if row["prediction_id"] in {TRADE_PREDICTION_ID, SKIP_PREDICTION_ID}
        ]
        assert len(service_orders) == 1
        order = service_orders[0]
        assert order["prediction_id"] == TRADE_PREDICTION_ID
        fills = connection.execute(
            select(schema.paper_fills)
            .where(schema.paper_fills.c.paper_order_id == order["paper_order_id"])
            .order_by(schema.paper_fills.c.id)
        ).mappings().all()
        terminals = connection.execute(
            select(schema.paper_order_terminal_events).where(
                schema.paper_order_terminal_events.c.paper_order_id == order["paper_order_id"]
            )
        ).mappings().all()
        settlements = connection.execute(
            select(schema.paper_settlements).where(
                schema.paper_settlements.c.paper_order_id == order["paper_order_id"]
            )
        ).mappings().all()
        predictions_after = tuple(
            dict(row)
            for row in connection.execute(
                select(schema.live_predictions)
                .where(
                    schema.live_predictions.c.prediction_id.in_(
                        (TRADE_PREDICTION_ID, SKIP_PREDICTION_ID)
                    )
                )
                .order_by(schema.live_predictions.c.prediction_id)
            ).mappings()
        )
        raw_after = tuple(
            dict(row)
            for row in connection.execute(
                select(schema.raw_market_events)
                .where(schema.raw_market_events.c.dedupe_key.in_(raw_keys))
                .order_by(schema.raw_market_events.c.id)
            ).mappings()
        )
        first_hashes = (
            order["semantic_sha256"],
            tuple(row["semantic_sha256"] for row in fills),
            tuple(row["semantic_sha256"] for row in terminals),
        )

    assert first.created_orders == 1
    assert first.created_fills > 0
    assert first.created_terminal_events == 1
    assert first.created_settlements == 0
    assert fills
    assert terminals
    assert not settlements
    assert predictions_after == predictions_before
    assert raw_after == raw_before
    assert all(str(fill["book_anchor_dedupe_key"]).startswith("sha256:") for fill in fills)
    assert all(fill["price"] <= order["limit_price"] for fill in fills)

    second = service.run_once(now=BASE + timedelta(seconds=3))
    assert second.created_orders == 0
    assert second.created_fills == 0
    assert second.created_terminal_events == 0
    assert second.created_settlements == 0

    with engine.begin() as connection:
        order_after = connection.execute(
            select(schema.paper_orders).where(
                schema.paper_orders.c.paper_order_id == order["paper_order_id"]
            )
        ).mappings().one()
        fills_after = connection.execute(
            select(schema.paper_fills)
            .where(schema.paper_fills.c.paper_order_id == order["paper_order_id"])
            .order_by(schema.paper_fills.c.id)
        ).mappings().all()
        terminals_after = connection.execute(
            select(schema.paper_order_terminal_events).where(
                schema.paper_order_terminal_events.c.paper_order_id == order["paper_order_id"]
            )
        ).mappings().all()
        second_hashes = (
            order_after["semantic_sha256"],
            tuple(row["semantic_sha256"] for row in fills_after),
            tuple(row["semantic_sha256"] for row in terminals_after),
        )
        assert second_hashes == first_hashes
        evaluation_repository.store(connection, _evaluation())

    third = service.run_once(now=BASE + timedelta(minutes=1, seconds=3))
    assert third.created_settlements == 1

    with engine.begin() as connection:
        settlement = connection.execute(
            select(schema.paper_settlements).where(
                schema.paper_settlements.c.paper_order_id == order["paper_order_id"]
            )
        ).mappings().one()
        total_fill_cost = sum((Decimal(row["total_cost"]) for row in fills_after), Decimal("0"))
        filled_shares = sum((Decimal(row["shares"]) for row in fills_after), Decimal("0"))
        assert Decimal(settlement["filled_shares"]) == filled_shares
        assert Decimal(settlement["total_fill_cost"]) == total_fill_cost
        assert Decimal(settlement["payout"]) == filled_shares
        assert Decimal(settlement["realized_pnl"]) == filled_shares - total_fill_cost
        settlement_hash = settlement["semantic_sha256"]

    fourth = service.run_once(now=BASE + timedelta(minutes=1, seconds=4))
    assert fourth.created_settlements == 0

    with engine.begin() as connection:
        settlement_after = connection.execute(
            select(schema.paper_settlements).where(
                schema.paper_settlements.c.paper_order_id == order["paper_order_id"]
            )
        ).mappings().one()
        assert settlement_after["semantic_sha256"] == settlement_hash
        _cleanup(connection, raw_keys=raw_keys)
