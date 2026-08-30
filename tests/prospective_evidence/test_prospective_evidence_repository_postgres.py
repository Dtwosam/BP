from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from bp_engine.prospective_evidence.repository import PostgresProspectiveEvidenceRepository
from sqlalchemy import create_engine, delete, func, insert, select

from bp_engine.storage import schema

DATABASE_URL = os.getenv("BP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BP_TEST_DATABASE_URL is required for PostgreSQL integration coverage",
)

ORDER_ID = "prospective-evidence-order"
PREDICTION_ID = "f" * 64
BASE = datetime(2026, 8, 30, 20, 0, tzinfo=UTC)


def _settlement(label_version: str, *, pnl: str, seconds: int) -> dict[str, object]:
    realized = Decimal(pnl)
    fill_cost = Decimal("1.00")
    return {
        "paper_order_id": ORDER_ID,
        "label_version": label_version,
        "official_outcome": "Up",
        "official_target": 1,
        "label_source": "test",
        "label_source_snapshot_sha256": label_version[0] * 64,
        "label_source_observed_at": BASE + timedelta(seconds=seconds),
        "filled_shares": Decimal("2.00"),
        "total_fill_cost": fill_cost,
        "total_fees": Decimal("0.02"),
        "payout": fill_cost + realized,
        "realized_pnl": realized,
        "settled_at": BASE + timedelta(seconds=seconds),
        "semantic_sha256": label_version[-1] * 64,
        "created_at": BASE + timedelta(seconds=seconds),
    }


def _evaluation(
    label_version: str,
    *,
    calibrated_brier: str,
    seconds: int,
) -> dict[str, object]:
    brier = Decimal(calibrated_brier)
    return {
        "prediction_id": PREDICTION_ID,
        "label_version": label_version,
        "official_outcome": "Up",
        "official_target": 1,
        "label_source": "test",
        "label_source_snapshot_sha256": label_version[0] * 64,
        "label_source_observed_at": BASE + timedelta(seconds=seconds),
        "evaluated_at": BASE + timedelta(seconds=seconds),
        "correct": True,
        "raw_log_loss": Decimal("0.30"),
        "raw_brier": brier + Decimal("0.02"),
        "calibrated_log_loss": Decimal("0.25"),
        "calibrated_brier": brier,
        "hypothetical_gross_pnl": None,
        "hypothetical_assumed_cost_pnl": None,
        "semantic_sha256": label_version[-1] * 64,
    }


def _counts(connection) -> dict[str, int | None]:
    return {
        "settlements": connection.scalar(
            select(func.count()).select_from(schema.paper_settlements)
        ),
        "evaluations": connection.scalar(
            select(func.count()).select_from(schema.live_prediction_evaluations)
        ),
    }


def test_repository_reads_latest_immutable_evidence_without_mutation() -> None:
    assert DATABASE_URL is not None
    engine = create_engine(DATABASE_URL)
    schema.metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            delete(schema.paper_settlements).where(
                schema.paper_settlements.c.paper_order_id == ORDER_ID
            )
        )
        connection.execute(
            delete(schema.live_prediction_evaluations).where(
                schema.live_prediction_evaluations.c.prediction_id == PREDICTION_ID
            )
        )
        connection.execute(
            insert(schema.paper_settlements),
            [
                _settlement("official-outcome-v1", pnl="1.00", seconds=1),
                _settlement("official-outcome-v2", pnl="2.00", seconds=2),
            ],
        )
        connection.execute(
            insert(schema.live_prediction_evaluations),
            [
                _evaluation("official-outcome-v1", calibrated_brier="0.12", seconds=1),
                _evaluation("official-outcome-v2", calibrated_brier="0.08", seconds=2),
            ],
        )
        before = _counts(connection)

    repository = PostgresProspectiveEvidenceRepository(engine)
    settlements = [
        row for row in repository.list_settlements() if row["paper_order_id"] == ORDER_ID
    ]
    evaluations = [
        row for row in repository.list_evaluations() if row["prediction_id"] == PREDICTION_ID
    ]
    reconciliation = repository.get_reconciliation()

    assert len(settlements) == 1
    assert settlements[0]["label_version"] == "official-outcome-v2"
    assert settlements[0]["realized_pnl"] == Decimal("2.000000000000000000")
    assert len(evaluations) == 1
    assert evaluations[0]["label_version"] == "official-outcome-v2"
    assert evaluations[0]["calibrated_brier"] == Decimal("0.080000000000000000")
    assert reconciliation["status"] in {"OK", "VIOLATION"}
    assert isinstance(reconciliation["violation_count"], int)

    with engine.begin() as connection:
        after = _counts(connection)
        connection.execute(
            delete(schema.paper_settlements).where(
                schema.paper_settlements.c.paper_order_id == ORDER_ID
            )
        )
        connection.execute(
            delete(schema.live_prediction_evaluations).where(
                schema.live_prediction_evaluations.c.prediction_id == PREDICTION_ID
            )
        )

    assert after == before
