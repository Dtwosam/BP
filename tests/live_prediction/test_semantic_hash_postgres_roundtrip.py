from __future__ import annotations

import os
import runpy
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, delete, insert, select

from bp_engine.live_prediction.cli import _ledger_float_candidates, _semantic_hash_matches
from bp_engine.live_prediction.models import LivePrediction
from bp_engine.live_prediction.repository import LivePredictionRepository
from bp_engine.storage import schema

DATABASE_URL = os.getenv("BP_TEST_DATABASE_URL")
LEDGER_QUANTUM = Decimal("0.000000000000000001")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BP_TEST_DATABASE_URL is required for PostgreSQL integration coverage",
)


def _prediction() -> LivePrediction:
    fixture_path = Path(__file__).with_name("test_ledger_numeric_semantics.py")
    namespace = runpy.run_path(str(fixture_path))
    return namespace["_prediction"]()


def _delete_prediction(connection, prediction: LivePrediction) -> None:
    connection.execute(
        delete(schema.live_predictions).where(
            schema.live_predictions.c.condition_id == prediction.condition_id
        )
    )


def _load_prediction_row(connection, prediction: LivePrediction):
    return connection.execute(
        select(schema.live_predictions).where(
            schema.live_predictions.c.prediction_id == prediction.prediction_id
        )
    ).mappings().one()


def test_prediction_semantic_hash_recovers_legacy_float_bound_postgres_row() -> None:
    assert DATABASE_URL is not None
    prediction = _prediction()
    engine = create_engine(DATABASE_URL)
    schema.metadata.create_all(engine)

    with engine.begin() as connection:
        _delete_prediction(connection, prediction)
        connection.execute(insert(schema.live_predictions).values(**asdict(prediction)))
        row = _load_prediction_row(connection, prediction)

        diagnostics: dict[str, object] = {}
        for name in (
            "training_prior",
            "raw_probability",
            "market_probability",
            "up_best_bid",
            "up_best_ask",
            "down_best_bid",
            "down_best_ask",
        ):
            original = getattr(prediction, name)
            if original is None:
                continue
            candidates = _ledger_float_candidates(row[name])
            diagnostics[name] = {
                "stored": str(row[name]),
                "original": repr(original),
                "candidate_count": len(candidates),
                "contains_original": any(
                    candidate.hex() == original.hex() for candidate in candidates
                ),
            }

        assert row["raw_probability"] == Decimal("0.578854192693484000")
        assert _semantic_hash_matches(row, LivePrediction) is True, diagnostics
        _delete_prediction(connection, prediction)


def test_prediction_repository_preserves_full_decimal_float_representation() -> None:
    assert DATABASE_URL is not None
    prediction = _prediction()
    engine = create_engine(DATABASE_URL)
    schema.metadata.create_all(engine)
    repository = LivePredictionRepository()
    expected_probability = Decimal(str(prediction.raw_probability)).quantize(LEDGER_QUANTUM)

    with engine.begin() as connection:
        _delete_prediction(connection, prediction)
        repository.store(connection, prediction)
        row = _load_prediction_row(connection, prediction)

        assert row["raw_probability"] == expected_probability
        assert _semantic_hash_matches(row, LivePrediction) is True
        _delete_prediction(connection, prediction)
