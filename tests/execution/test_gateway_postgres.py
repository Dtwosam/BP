from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select

from bp_engine.execution.models import PaperExecutionConfig
from bp_engine.execution.paper import PaperOrderDraft, build_paper_order
from bp_engine.execution.protocol import ExecutionGateway
from bp_engine.execution.service import PaperExecutionService
from bp_engine.live_prediction.repository import LivePredictionRepository
from bp_engine.storage import schema
from bp_engine.storage.recorder import RecorderRepository
from tests.execution.test_service_postgres import (
    BASE,
    DATABASE_URL,
    TRADE_CONDITION,
    TRADE_PREDICTION_ID,
    _cleanup,
    _prediction,
    _raw_event,
)

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BP_TEST_DATABASE_URL is required for PostgreSQL integration coverage",
)


def test_paper_gateway_submits_and_cancels_idempotently_with_causal_fills() -> None:
    assert DATABASE_URL is not None
    engine = create_engine(DATABASE_URL)
    schema.metadata.create_all(engine)
    prediction_repository = LivePredictionRepository()
    recorder = RecorderRepository()
    config = PaperExecutionConfig()
    service = PaperExecutionService(engine=engine, config=config)
    assert isinstance(service, ExecutionGateway)

    prediction = _prediction(
        prediction_id=TRADE_PREDICTION_ID,
        semantic_sha256="d" * 64,
        condition_id=TRADE_CONDITION,
        trade=True,
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
                {"price": "0.62", "size": "10"},
            ],
        },
    )
    after_cancel = _raw_event(
        event_type="price_change",
        received_at=BASE + timedelta(milliseconds=900),
        asset_id=None,
        payload={
            "event_type": "price_change",
            "market": TRADE_CONDITION,
            "price_changes": [
                {
                    "asset_id": f"{TRADE_CONDITION}-up",
                    "side": "SELL",
                    "price": "0.60",
                    "size": "10",
                }
            ],
        },
    )
    raw_events = (anchor, after_cancel)
    raw_keys = tuple(event.dedupe_key for event in raw_events)

    try:
        with engine.begin() as connection:
            _cleanup(connection, raw_keys=raw_keys)
            prediction_repository.store(connection, prediction)
            recorder.insert_events(connection, raw_events)

        draft = build_paper_order(asdict(prediction), config, Decimal("100"))
        assert isinstance(draft, PaperOrderDraft)

        first_submit = service.submit_order(draft.request)
        second_submit = service.submit_order(draft.request)
        assert first_submit.accepted is True
        assert first_submit.reason == "accepted"
        assert second_submit.accepted is True
        assert second_submit.order_id == first_submit.order_id
        assert second_submit.reason == "existing"

        cancel_at = BASE + timedelta(milliseconds=700)
        first_cancel = service.cancel_order(first_submit.order_id, cancel_at)
        second_cancel = service.cancel_order(first_submit.order_id, cancel_at)
        assert first_cancel.cancelled is True
        assert first_cancel.reason == "cancelled_with_remainder"
        assert second_cancel.cancelled is True
        assert second_cancel.reason == "already_cancelled"

        with engine.begin() as connection:
            fills = connection.execute(
                select(schema.paper_fills)
                .where(schema.paper_fills.c.paper_order_id == first_submit.order_id)
                .order_by(schema.paper_fills.c.id)
            ).mappings().all()
            terminal = connection.execute(
                select(schema.paper_order_terminal_events).where(
                    schema.paper_order_terminal_events.c.paper_order_id == first_submit.order_id
                )
            ).mappings().one()

        assert [(Decimal(fill["price"]), Decimal(fill["shares"])) for fill in fills] == [
            (Decimal("0.60"), Decimal("2")),
        ]
        assert all(fill["fill_at"] <= cancel_at for fill in fills)
        assert terminal["terminal_status"] == "CANCELLED"
        assert terminal["event_at"] == cancel_at
        assert Decimal(terminal["remaining_shares"]) > Decimal("0")

        later = service.run_once(now=BASE + timedelta(seconds=3))
        assert later.created_fills == 0
        assert later.created_terminal_events == 0
    finally:
        with engine.begin() as connection:
            _cleanup(connection, raw_keys=raw_keys)
