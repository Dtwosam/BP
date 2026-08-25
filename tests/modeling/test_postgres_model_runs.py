from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, delete, select

from bp_engine.modeling.models import MetricSummary, ModelEvaluation, TrainingRunReport
from bp_engine.modeling.repository import ModelTrainingRunRepository, TrainingRunConflict
from bp_engine.storage.schema import metadata, model_training_runs

DATABASE_URL = os.getenv("BP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BP_TEST_DATABASE_URL is required for PostgreSQL integration coverage",
)


def _metric(log_loss: float) -> MetricSummary:
    return MetricSummary(
        row_count=8,
        market_count=8,
        accuracy=0.625,
        balanced_accuracy=0.625,
        log_loss=log_loss,
        brier_score=0.21,
        ece=0.04,
        calibration=(),
        confidence_coverage={},
    )


def _report(created_at: datetime) -> TrainingRunReport:
    evaluation = ModelEvaluation(
        family="logistic",
        config={"solver": "lbfgs"},
        validation=_metric(0.55),
        test=_metric(0.56),
    )
    return TrainingRunReport(
        run_id="phase7-postgres-run-registry",
        dataset_version="supervised-core-v1",
        split_version="chronological-market-v1",
        feature_version="core-v1",
        label_version="official-outcome-v1",
        horizon_seconds=300,
        start=datetime(2026, 8, 24, tzinfo=UTC),
        end=datetime(2026, 8, 25, tzinfo=UTC),
        dataset_sha256="d" * 64,
        split_sha256="s" * 64,
        predictor_names=("pm_up_price",),
        dropped_all_missing=(),
        model_configs={"logistic": {"solver": "lbfgs"}},
        validation_champion="logistic",
        best_test_result="logistic",
        boosted_promotion_eligible=False,
        evaluations={"logistic": evaluation},
        offset_metrics={},
        gross_execution_diagnostic={"coverage": 0.0},
        artifacts=(
            {
                "family": "logistic",
                "file_name": "logistic.joblib",
                "size_bytes": 10,
                "sha256": "a" * 64,
            },
        ),
        semantic_sha256="e" * 64,
        created_at=created_at,
    )


def test_postgres_model_run_registry_is_idempotent_and_conflict_safe() -> None:
    assert DATABASE_URL is not None
    engine = create_engine(DATABASE_URL)
    metadata.create_all(engine)
    repository = ModelTrainingRunRepository()
    created_at = datetime(2026, 8, 25, 17, 5, tzinfo=UTC)
    report = _report(created_at)

    with engine.begin() as connection:
        connection.execute(
            delete(model_training_runs).where(model_training_runs.c.run_id == report.run_id)
        )
        first = repository.store(connection, report)
        second = repository.store(
            connection,
            replace(report, created_at=created_at + timedelta(minutes=1)),
        )
        stored_created_at = connection.execute(
            select(model_training_runs.c.created_at).where(
                model_training_runs.c.run_id == report.run_id
            )
        ).scalar_one()
        with pytest.raises(TrainingRunConflict, match="run_id"):
            repository.store(
                connection,
                replace(
                    report,
                    dataset_sha256="x" * 64,
                    semantic_sha256="y" * 64,
                ),
            )
        connection.execute(
            delete(model_training_runs).where(model_training_runs.c.run_id == report.run_id)
        )

    assert first.created is True and first.existing is False
    assert second.created is False and second.existing is True
    assert stored_created_at == created_at
