from __future__ import annotations

import os
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, select

from bp_engine.execution.book import BookReplayError
from bp_engine.execution.models import PaperExecutionConfig
from bp_engine.execution.service import PaperExecutionService
from bp_engine.live_prediction.repository import LivePredictionRepository
from bp_engine.storage import schema
from tests.execution.test_service_postgres import (
    BASE,
    TRADE_CONDITION,
    TRADE_PREDICTION_ID,
    _cleanup,
    _prediction,
)

DATABASE_URL = os.getenv("BP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BP_TEST_DATABASE_URL is required for PostgreSQL integration coverage",
)


class _RejectingBookReader:
    def book_at(self, connection, *, condition_id, asset_id, observed_at):
        del connection, condition_id, asset_id, observed_at
        raise BookReplayError("replayed book is crossed or locked")


def test_paper_service_terminalizes_invalid_book_evidence_without_fills() -> None:
    assert DATABASE_URL is not None
    engine = create_engine(DATABASE_URL)
    schema.metadata.create_all(engine)
    prediction_repository = LivePredictionRepository()
    service = PaperExecutionService(
        engine=engine,
        config=PaperExecutionConfig(),
        book_reader=_RejectingBookReader(),
    )
    prediction = _prediction(
        prediction_id=TRADE_PREDICTION_ID,
        semantic_sha256="f" * 64,
        condition_id=TRADE_CONDITION,
        trade=True,
    )

    with engine.begin() as connection:
        _cleanup(connection, raw_keys=())
        prediction_repository.store(connection, prediction)

    first = service.run_once(now=BASE + timedelta(seconds=3))

    with engine.begin() as connection:
        order = connection.execute(
            select(schema.paper_orders).where(
                schema.paper_orders.c.prediction_id == TRADE_PREDICTION_ID
            )
        ).mappings().one()
        fills = connection.execute(
            select(schema.paper_fills).where(
                schema.paper_fills.c.paper_order_id == order["paper_order_id"]
            )
        ).mappings().all()
        terminal = connection.execute(
            select(schema.paper_order_terminal_events).where(
                schema.paper_order_terminal_events.c.paper_order_id
                == order["paper_order_id"]
            )
        ).mappings().one()

    assert first.created_orders == 1
    assert first.created_fills == 0
    assert first.created_terminal_events == 1
    assert fills == []
    assert terminal["terminal_status"] == "EXPIRED"
    assert terminal["remaining_shares"] == order["requested_shares"]
    assert terminal["event_at"] == order["expires_at"]
    assert terminal["reason"] == (
        "causal book replay failed closed: replayed book is crossed or locked"
    )

    second = service.run_once(now=BASE + timedelta(seconds=4))
    assert second.created_orders == 0
    assert second.created_fills == 0
    assert second.created_terminal_events == 0
    assert second.existing_terminal_events == 1

    with engine.begin() as connection:
        terminal_count = connection.execute(
            select(schema.paper_order_terminal_events.c.id).where(
                schema.paper_order_terminal_events.c.paper_order_id
                == order["paper_order_id"]
            )
        ).scalars().all()
        assert len(terminal_count) == 1
        _cleanup(connection, raw_keys=())
