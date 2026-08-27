from __future__ import annotations

import os
import runpy
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, delete, insert, select

from bp_engine.features.hashing import canonical_hash
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


def _down_prediction() -> LivePrediction:
    prediction = _prediction()
    probability_up = 0.4211458073065157
    side_probability = 1.0 - probability_up
    ask = 0.58
    bid = 0.57
    spread = ask - bid
    fee = 0.07 * ask * (1.0 - ask)
    raw_edge = side_probability - ask
    cost_adjusted_edge = raw_edge - fee - 0.01
    decision = dict(prediction.edge_decision)
    decision.update(
        {
            "side": "down",
            "predicted_target": 0,
            "side_probability": side_probability,
            "ask": ask,
            "bid": bid,
            "spread": spread,
            "fee": fee,
            "raw_edge": raw_edge,
            "cost_adjusted_edge": cost_adjusted_edge,
        }
    )
    prediction = replace(
        prediction,
        raw_probability=probability_up,
        calibrated_probability=probability_up,
        market_probability=probability_up,
        predicted_target=0,
        predicted_side="down",
        down_best_bid=bid,
        down_best_ask=ask,
        selected_side="down",
        selected_ask=ask,
        selected_bid=bid,
        selected_spread=spread,
        fee=fee,
        raw_edge=raw_edge,
        cost_adjusted_edge=cost_adjusted_edge,
        edge_decision=decision,
    )
    values = asdict(prediction)
    values.pop("semantic_sha256")
    return replace(prediction, semantic_sha256=canonical_hash(values))


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


def test_prediction_semantic_hash_recovers_legacy_down_probability_alias_postgres_row() -> None:
    assert DATABASE_URL is not None
    prediction = _down_prediction()
    engine = create_engine(DATABASE_URL)
    schema.metadata.create_all(engine)

    with engine.begin() as connection:
        _delete_prediction(connection, prediction)
        connection.execute(insert(schema.live_predictions).values(**asdict(prediction)))
        row = _load_prediction_row(connection, prediction)

        side_probability = float(prediction.edge_decision["side_probability"])
        aliases = tuple(
            candidate
            for candidate in _ledger_float_candidates(row["calibrated_probability"])
            if 1.0 - candidate == side_probability
        )
        assert len(aliases) > 1
        assert any(
            candidate.hex() == prediction.calibrated_probability.hex()
            for candidate in aliases
        )
        assert _semantic_hash_matches(row, LivePrediction) is True
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
