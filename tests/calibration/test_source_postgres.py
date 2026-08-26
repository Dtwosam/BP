from __future__ import annotations

import importlib
import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, delete, insert

from bp_engine.storage.schema import backtest_runs, metadata

DATABASE_URL = os.getenv("BP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BP_TEST_DATABASE_URL is required for PostgreSQL integration coverage",
)


def test_postgres_source_loader_reads_restricted_backtest_contract() -> None:
    assert DATABASE_URL is not None
    module = importlib.import_module("bp_engine.calibration.source")
    engine = create_engine(DATABASE_URL)
    metadata.create_all(engine)
    run_id = "phase8-300-source-postgres"
    start = datetime(2026, 8, 24, tzinfo=UTC)
    end = datetime(2026, 8, 25, tzinfo=UTC)
    fold_hash = "e" * 64
    final_hash = "f" * 64
    report = {
        "run_id": run_id,
        "backtest_version": "walk-forward-v1",
        "source_training_run_id": "phase7-300-source",
        "source_training_semantic_sha256": "a" * 64,
        "dataset_version": "supervised-core-v1",
        "feature_version": "core-v1",
        "label_version": "official-outcome-v1",
        "horizon_seconds": 300,
        "start": "2026-08-24T00:00:00Z",
        "end": "2026-08-25T00:00:00Z",
        "dataset_sha256": "b" * 64,
        "config": {"train_duration_seconds": 28800.0},
        "config_sha256": "c" * 64,
        "plan_sha256": "d" * 64,
        "fold_membership_sha256": [fold_hash, final_hash],
        "folds": [
            {
                "index": 0,
                "membership_sha256": fold_hash,
                "train_condition_ids": ["train-a"],
                "validation_condition_ids": ["val-a"],
                "test_condition_ids": ["test-a"],
                "selected_offset_seconds": 240,
                "metrics": {"accuracy": 1.0},
            }
        ],
        "aggregate_oos_metrics": {"accuracy": 1.0},
        "final_holdout": {
            "membership_sha256": final_hash,
            "train_condition_ids": ["final-train-a"],
            "validation_condition_ids": ["final-val-a"],
            "holdout_condition_ids": ["holdout-a"],
            "selected_offset_seconds": 180,
            "metrics": {"accuracy": 0.0},
        },
        "semantic_sha256": "9" * 64,
    }
    payload = {
        "run_id": run_id,
        "backtest_version": "walk-forward-v1",
        "source_training_run_id": "phase7-300-source",
        "source_training_semantic_sha256": "a" * 64,
        "dataset_version": "supervised-core-v1",
        "feature_version": "core-v1",
        "label_version": "official-outcome-v1",
        "horizon_seconds": 300,
        "requested_start": start,
        "requested_end": end,
        "dataset_sha256": "b" * 64,
        "config": report["config"],
        "config_sha256": "c" * 64,
        "plan_sha256": "d" * 64,
        "fold_membership_sha256": [fold_hash, final_hash],
        "report": report,
        "semantic_sha256": "9" * 64,
        "created_at": end,
    }

    with engine.begin() as connection:
        connection.execute(delete(backtest_runs).where(backtest_runs.c.run_id == run_id))
        connection.execute(insert(backtest_runs).values(**payload))
        spec = module.load_backtest_source_spec(connection, run_id)
        connection.execute(delete(backtest_runs).where(backtest_runs.c.run_id == run_id))

    assert spec.run_id == run_id
    assert spec.folds[0].selected_offset_seconds == 240
    assert spec.final.holdout_condition_ids == ("holdout-a",)
    assert not hasattr(spec, "aggregate_oos_metrics")
