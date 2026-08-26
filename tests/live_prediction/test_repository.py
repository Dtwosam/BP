from __future__ import annotations

import importlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select

from bp_engine.storage import schema


def _models():
    return importlib.import_module("bp_engine.live_prediction.models")


def _repository_module():
    return importlib.import_module("bp_engine.live_prediction.repository")


def _prediction(recorded_at: datetime):
    models = _models()
    start = datetime(2026, 8, 26, 14, 0, tzinfo=UTC)
    scheduled = start + timedelta(minutes=4)
    return models.LivePrediction(
        prediction_id="1" * 64,
        semantic_sha256="2" * 64,
        prediction_version="live-prediction-v1",
        live_input_version="phase10-live-market-input-v1",
        condition_id="condition-ledger-1",
        slug="btc-updown-5m-condition-ledger-1",
        horizon_seconds=300,
        market_start_at=start,
        market_end_at=start + timedelta(minutes=5),
        scheduled_at=scheduled,
        recorded_at=recorded_at,
        lateness_ms=int((recorded_at - scheduled).total_seconds() * 1000),
        up_token_id="up-token",
        down_token_id="down-token",
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
        raw_probability=0.61,
        calibrated_probability=0.61,
        predicted_target=1,
        predicted_side="up",
        market_probability_observed=True,
        market_probability=0.61,
        market_probability_observed_at=scheduled,
        market_probability_downloaded_at=scheduled + timedelta(seconds=1),
        market_probability_source="polymarket_clob",
        market_probability_dataset="prices_history",
        market_probability_request_params={"market": "up-token", "fidelity": "1"},
        market_probability_response_sha256="9" * 64,
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
        raw_edge=0.01,
        cost_adjusted_edge=-0.0168,
        decision_min_edge=0.02,
        edge_decision={"side": "up", "trade": True, "reason": "trade"},
        input_fingerprint="a" * 64,
    )


def _evaluation(evaluated_at: datetime):
    models = _models()
    return models.LivePredictionEvaluation(
        prediction_id="1" * 64,
        label_version="official-outcome-v1",
        official_outcome="Up",
        official_target=1,
        label_source="polymarket_gamma_snapshot",
        label_source_snapshot_sha256="b" * 64,
        label_source_observed_at=evaluated_at - timedelta(seconds=1),
        evaluated_at=evaluated_at,
        correct=True,
        raw_log_loss=0.494296,
        raw_brier=0.1521,
        calibrated_log_loss=0.494296,
        calibrated_brier=0.1521,
        hypothetical_gross_pnl=0.40,
        hypothetical_assumed_cost_pnl=0.3732,
        semantic_sha256="c" * 64,
    )


def test_live_prediction_schema_has_append_only_natural_keys() -> None:
    prediction = schema.live_predictions
    evaluation = schema.live_prediction_evaluations
    required_prediction = {
        "prediction_id",
        "semantic_sha256",
        "prediction_version",
        "live_input_version",
        "condition_id",
        "scheduled_at",
        "recorded_at",
        "source_calibration_run_id",
        "source_calibration_semantic_sha256",
        "raw_probability",
        "calibrated_probability",
        "market_probability_observed",
        "market_probability_response_sha256",
        "edge_decision",
        "input_fingerprint",
    }
    required_evaluation = {
        "prediction_id",
        "label_version",
        "official_outcome",
        "official_target",
        "label_source_snapshot_sha256",
        "label_source_observed_at",
        "evaluated_at",
        "correct",
        "raw_log_loss",
        "calibrated_log_loss",
        "semantic_sha256",
    }
    assert required_prediction <= set(prediction.c.keys())
    assert required_evaluation <= set(evaluation.c.keys())
    prediction_unique = {
        tuple(constraint.columns.keys())
        for constraint in prediction.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    evaluation_unique = {
        tuple(constraint.columns.keys())
        for constraint in evaluation.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("condition_id", "prediction_version") in prediction_unique
    assert ("prediction_id", "label_version") in evaluation_unique
    assert "official_outcome" not in prediction.c
    assert "official_target" not in prediction.c
    assert "correct" not in prediction.c


def test_live_prediction_migration_declares_both_ledgers_and_no_parent_outcome() -> None:
    path = Path(__file__).parents[2] / "migrations" / "0010_live_predictions.sql"
    text = path.read_text(encoding="utf-8")
    prediction_ddl, evaluation_ddl = text.split(
        "CREATE TABLE IF NOT EXISTS live_prediction_evaluations",
        maxsplit=1,
    )

    assert "CREATE TABLE IF NOT EXISTS live_predictions" in prediction_ddl
    assert "UNIQUE (condition_id, prediction_version)" in prediction_ddl
    assert "official_outcome" not in prediction_ddl
    assert "official_target" not in prediction_ddl
    assert "prediction_id VARCHAR(64) NOT NULL" in evaluation_ddl
    assert "UNIQUE (prediction_id, label_version)" in evaluation_ddl


def test_prediction_repository_is_idempotent_and_conflict_safe() -> None:
    module = _repository_module()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    schema.metadata.create_all(engine)
    repository = module.LivePredictionRepository()
    recorded_at = datetime(2026, 8, 26, 14, 4, 2, tzinfo=UTC)
    prediction = _prediction(recorded_at)

    with engine.begin() as connection:
        first = repository.store(connection, prediction)
        second = repository.store(connection, prediction)
        stored = connection.execute(
            select(schema.live_predictions).where(
                schema.live_predictions.c.prediction_id == prediction.prediction_id
            )
        ).mappings().one()
        with pytest.raises(module.LivePredictionConflict, match="condition_id"):
            repository.store(
                connection,
                replace(prediction, semantic_sha256="d" * 64),
            )

    assert first.created is True and first.existing is False
    assert second.created is False and second.existing is True
    assert stored["recorded_at"].replace(tzinfo=UTC) == recorded_at
    assert not hasattr(repository, "update")
    assert not hasattr(repository, "delete")


def test_prediction_repository_rejects_same_hash_with_changed_evidence() -> None:
    module = _repository_module()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    schema.metadata.create_all(engine)
    repository = module.LivePredictionRepository()
    prediction = _prediction(datetime(2026, 8, 26, 14, 4, 2, tzinfo=UTC))

    with engine.begin() as connection:
        repository.store(connection, prediction)
        with pytest.raises(module.LivePredictionConflict, match="condition_id"):
            repository.store(
                connection,
                replace(
                    prediction,
                    recorded_at=prediction.recorded_at + timedelta(seconds=1),
                    lateness_ms=prediction.lateness_ms + 1000,
                ),
            )


def test_evaluation_insert_never_rewrites_prediction_parent() -> None:
    module = _repository_module()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    schema.metadata.create_all(engine)
    prediction_repository = module.LivePredictionRepository()
    evaluation_repository = module.LivePredictionEvaluationRepository()
    prediction = _prediction(datetime(2026, 8, 26, 14, 4, 2, tzinfo=UTC))
    evaluation = _evaluation(datetime(2026, 8, 26, 14, 6, tzinfo=UTC))

    with engine.begin() as connection:
        prediction_repository.store(connection, prediction)
        before = dict(
            connection.execute(
                select(schema.live_predictions).where(
                    schema.live_predictions.c.prediction_id == prediction.prediction_id
                )
            ).mappings().one()
        )
        first = evaluation_repository.store(connection, evaluation)
        second = evaluation_repository.store(connection, evaluation)
        after = dict(
            connection.execute(
                select(schema.live_predictions).where(
                    schema.live_predictions.c.prediction_id == prediction.prediction_id
                )
            ).mappings().one()
        )
        with pytest.raises(module.LivePredictionEvaluationConflict, match="prediction_id"):
            evaluation_repository.store(
                connection,
                replace(evaluation, official_outcome="Down", official_target=0),
            )

    assert first.created is True and first.existing is False
    assert second.created is False and second.existing is True
    assert after == before
    assert not hasattr(evaluation_repository, "update")
    assert not hasattr(evaluation_repository, "delete")


def test_repositories_reject_invalid_hashes_and_naive_timestamps() -> None:
    module = _repository_module()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    schema.metadata.create_all(engine)
    prediction_repository = module.LivePredictionRepository()
    evaluation_repository = module.LivePredictionEvaluationRepository()
    prediction = _prediction(datetime(2026, 8, 26, 14, 4, 2, tzinfo=UTC))
    evaluation = _evaluation(datetime(2026, 8, 26, 14, 6, tzinfo=UTC))

    with engine.begin() as connection:
        with pytest.raises(ValueError, match="semantic_sha256"):
            prediction_repository.store(
                connection,
                replace(prediction, semantic_sha256="not-a-digest"),
            )
        with pytest.raises(ValueError, match="recorded_at"):
            prediction_repository.store(
                connection,
                replace(prediction, recorded_at=prediction.recorded_at.replace(tzinfo=None)),
            )
        with pytest.raises(ValueError, match="label_source_snapshot_sha256"):
            evaluation_repository.store(
                connection,
                replace(evaluation, label_source_snapshot_sha256="short"),
            )
