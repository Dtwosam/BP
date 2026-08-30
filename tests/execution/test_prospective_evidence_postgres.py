from __future__ import annotations

import importlib
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, delete, insert

from bp_engine.execution.repository import PaperExecutionRepository
from bp_engine.live_prediction.evaluation import append_available_evaluations
from bp_engine.live_prediction.repository import LivePredictionRepository
from bp_engine.storage import schema
from tests.execution.test_repository_postgres import _prediction, _records
from tests.live_prediction.test_evaluation_postgres import _label

DATABASE_URL = os.getenv("BP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BP_TEST_DATABASE_URL is required for PostgreSQL integration coverage",
)


def _at(start: datetime, *, prediction_id: str, semantic: str, condition: str):
    base = _prediction()
    scheduled = start + timedelta(minutes=4)
    return replace(
        base,
        prediction_id=prediction_id,
        semantic_sha256=semantic,
        condition_id=condition,
        slug=f"btc-updown-5m-{condition}",
        market_start_at=start,
        market_end_at=start + timedelta(minutes=5),
        scheduled_at=scheduled,
        recorded_at=scheduled + timedelta(milliseconds=100),
        market_probability_observed_at=scheduled,
        market_probability_downloaded_at=scheduled,
        up_book_cutoff_at=scheduled,
        down_book_cutoff_at=scheduled,
        input_fingerprint=("f" if semantic[0] != "f" else "e") * 64,
    )


def _cleanup(connection, *, prediction_ids: tuple[str, ...], order_id: str) -> None:
    connection.execute(
        delete(schema.paper_settlements).where(
            schema.paper_settlements.c.paper_order_id == order_id
        )
    )
    connection.execute(
        delete(schema.paper_order_terminal_events).where(
            schema.paper_order_terminal_events.c.paper_order_id == order_id
        )
    )
    connection.execute(
        delete(schema.paper_fills).where(schema.paper_fills.c.paper_order_id == order_id)
    )
    connection.execute(
        delete(schema.paper_orders).where(schema.paper_orders.c.paper_order_id == order_id)
    )
    connection.execute(
        delete(schema.live_prediction_evaluations).where(
            schema.live_prediction_evaluations.c.prediction_id.in_(prediction_ids)
        )
    )
    connection.execute(
        delete(schema.live_predictions).where(
            schema.live_predictions.c.prediction_id.in_(prediction_ids)
        )
    )
    connection.execute(
        delete(schema.market_labels).where(
            schema.market_labels.c.condition_id.in_(
                ("prospective-reader-pre", "prospective-reader-post")
            )
        )
    )


def test_postgres_reader_excludes_pre_boundary_evidence() -> None:
    assert DATABASE_URL is not None
    evidence = importlib.import_module("bp_engine.execution.evidence")
    assert hasattr(evidence, "PostgresProspectivePaperEvidenceReader"), (
        "prospective PostgreSQL evidence reader is missing"
    )

    engine = create_engine(DATABASE_URL)
    schema.metadata.create_all(engine)
    since = datetime(2040, 1, 1, 0, 0, tzinfo=UTC)
    pre = _at(
        datetime(2039, 12, 31, 23, 50, tzinfo=UTC),
        prediction_id="a" * 64,
        semantic="b" * 64,
        condition="prospective-reader-pre",
    )
    post = _at(
        datetime(2040, 1, 1, 0, 10, tzinfo=UTC),
        prediction_id="c" * 64,
        semantic="d" * 64,
        condition="prospective-reader-post",
    )
    order_id = "prospective-reader-post-order"
    models = importlib.import_module("bp_engine.execution.models")
    order, fill, terminal, settlement = _records(models, post)
    order = replace(order, paper_order_id=order_id, semantic_sha256="1" * 64)
    fill = replace(fill, paper_order_id=order_id, semantic_sha256="2" * 64)
    terminal = replace(terminal, paper_order_id=order_id, semantic_sha256="3" * 64)
    settlement = replace(settlement, paper_order_id=order_id, semantic_sha256="4" * 64)
    prediction_ids = (pre.prediction_id, post.prediction_id)

    try:
        with engine.begin() as connection:
            _cleanup(connection, prediction_ids=prediction_ids, order_id=order_id)
            LivePredictionRepository().store(connection, pre)
            LivePredictionRepository().store(connection, post)
            connection.execute(insert(schema.market_labels).values(**_label(pre).__dict__))
            connection.execute(insert(schema.market_labels).values(**_label(post).__dict__))
            append_available_evaluations(
                connection,
                evaluated_at=post.market_end_at + timedelta(minutes=1),
            )
            repository = PaperExecutionRepository()
            repository.insert_order(connection, order)
            repository.insert_fill(connection, fill)
            repository.insert_terminal_event(connection, terminal)
            repository.insert_settlement(connection, settlement)

        inputs = evidence.PostgresProspectivePaperEvidenceReader(engine).load(since=since)

        assert [row["prediction_id"] for row in inputs.predictions] == [post.prediction_id]
        assert [row["prediction_id"] for row in inputs.evaluations] == [post.prediction_id]
        assert [row["paper_order_id"] for row in inputs.settled_trades] == [order_id]
        assert inputs.settled_trades[0]["condition_id"] == post.condition_id
        assert inputs.settled_trades[0]["realized_pnl"] == settlement.realized_pnl
    finally:
        with engine.begin() as connection:
            _cleanup(connection, prediction_ids=prediction_ids, order_id=order_id)


def test_postgres_reader_rejects_naive_since() -> None:
    assert DATABASE_URL is not None
    evidence = importlib.import_module("bp_engine.execution.evidence")
    assert hasattr(evidence, "PostgresProspectivePaperEvidenceReader"), (
        "prospective PostgreSQL evidence reader is missing"
    )
    reader = evidence.PostgresProspectivePaperEvidenceReader(create_engine(DATABASE_URL))

    with pytest.raises(ValueError, match="timezone-aware"):
        reader.load(since=datetime(2040, 1, 1))
