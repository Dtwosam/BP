from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select

from bp_engine.modeling.models import MetricSummary, ModelEvaluation, TrainingRunReport
from bp_engine.modeling.repository import ModelTrainingRunRepository, TrainingRunConflict
from bp_engine.storage.schema import metadata, model_training_runs


def _metric(value: float = 0.4) -> MetricSummary:
    return MetricSummary(
        row_count=10,
        market_count=10,
        accuracy=0.6,
        balanced_accuracy=0.6,
        log_loss=value,
        brier_score=0.2,
        ece=0.05,
        calibration=(),
        confidence_coverage={},
    )


def _report(created_at: datetime) -> TrainingRunReport:
    evaluation = ModelEvaluation(
        family="logistic",
        config={"solver": "lbfgs"},
        validation=_metric(),
        test=_metric(0.41),
    )
    return TrainingRunReport(
        run_id="run-1",
        dataset_version="supervised-core-v1",
        split_version="chronological-market-v1",
        feature_version="core-v1",
        label_version="official-outcome-v1",
        horizon_seconds=300,
        start=datetime(2026, 8, 24, tzinfo=UTC),
        end=datetime(2026, 8, 25, tzinfo=UTC),
        dataset_sha256="d" * 64,
        split_sha256="s" * 64,
        predictor_names=("x",),
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
                "size_bytes": 12,
                "sha256": "a" * 64,
            },
        ),
        semantic_sha256="e" * 64,
        created_at=created_at,
    )


def test_model_training_run_schema_has_immutable_run_identity() -> None:
    expected = {
        "run_id",
        "dataset_version",
        "split_version",
        "feature_version",
        "label_version",
        "horizon_seconds",
        "requested_start",
        "requested_end",
        "dataset_sha256",
        "split_sha256",
        "predictor_names",
        "dropped_all_missing",
        "model_configs",
        "validation_champion",
        "report",
        "artifact_manifest",
        "semantic_sha256",
        "created_at",
    }
    assert expected <= set(model_training_runs.c.keys())
    unique_sets = {
        tuple(constraint.columns.keys())
        for constraint in model_training_runs.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("run_id",) in unique_sets


def test_training_run_repository_is_idempotent_and_conflict_safe() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    repository = ModelTrainingRunRepository()
    created_at = datetime(2026, 8, 25, 17, 0, tzinfo=UTC)
    report = _report(created_at)

    with engine.begin() as connection:
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

    assert first.created is True and first.existing is False
    assert second.created is False and second.existing is True
    assert stored_created_at.replace(tzinfo=UTC) == created_at
