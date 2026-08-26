from __future__ import annotations

import importlib
import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, delete, insert

from bp_engine.storage.schema import (
    calibration_edge_runs,
    market_labels,
    metadata,
    model_training_runs,
)

DATABASE_URL = os.getenv("BP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BP_TEST_DATABASE_URL is required for PostgreSQL integration coverage",
)


def test_postgres_live_policy_loader_reconstructs_train_only_prior() -> None:
    assert DATABASE_URL is not None
    module = importlib.import_module("bp_engine.live_prediction.policy")
    engine = create_engine(DATABASE_URL)
    metadata.create_all(engine)
    start = datetime(2026, 8, 24, tzinfo=UTC)
    end = datetime(2026, 8, 25, tzinfo=UTC)
    run_id = "phase9-300-live-policy-postgres"
    training_id = "phase7-300-live-policy-postgres"
    train_ids = [f"pg-train-{index}" for index in range(4)]
    config = {
        "fee_rate": 0.07,
        "slippage_buffer": 0.01,
        "min_edge_grid": [0.0, 0.05],
        "min_validation_trades": 3,
        "max_spread": None,
    }
    report = {
        "run_id": run_id,
        "calibration_version": "platt-or-identity-v1",
        "edge_policy_version": "selected-ask-edge-v1",
        "source_backtest_run_id": "phase8-300-live-policy-postgres",
        "source_backtest_version": "walk-forward-v1",
        "source_backtest_semantic_sha256": "b" * 64,
        "source_training_run_id": training_id,
        "source_training_semantic_sha256": "a" * 64,
        "dataset_version": "supervised-core-v1",
        "feature_version": "core-v1",
        "label_version": "official-outcome-v1",
        "horizon_seconds": 300,
        "start": "2026-08-24T00:00:00Z",
        "end": "2026-08-25T00:00:00Z",
        "dataset_sha256": "c" * 64,
        "config": config,
        "config_sha256": "d" * 64,
        "source_backtest_config_sha256": "e" * 64,
        "source_plan_sha256": "f" * 64,
        "source_fold_membership_sha256": ["1" * 64],
        "folds": [],
        "aggregate_oos": {},
        "final_holdout": {
            "train_condition_ids": train_ids,
            "validation_condition_ids": ["pg-val-0"],
            "holdout_condition_ids": ["pg-holdout-0"],
            "selected_offset_seconds": 240,
            "calibration_selection_fit_partition": "train",
            "calibration_selection_partition": "validation",
            "edge_selection_partition": "validation",
            "evaluation_partition": "holdout",
            "calibration_selection": {
                "method": "identity",
                "fit": {"method": "identity", "intercept": None, "coefficient": None},
                "validation_metrics": {},
                "candidates": [],
            },
            "edge_policy_selection": {
                "policy": "no_trade",
                "min_edge": None,
                "validation_metrics": {},
                "candidates": [],
            },
            "raw_metrics": {},
            "calibrated_metrics": {},
            "edge_metrics": {},
            "predictions": [],
        },
        "semantic_sha256": "9" * 64,
    }

    with engine.begin() as connection:
        connection.execute(delete(calibration_edge_runs).where(calibration_edge_runs.c.run_id == run_id))
        connection.execute(delete(model_training_runs).where(model_training_runs.c.run_id == training_id))
        connection.execute(delete(market_labels).where(market_labels.c.condition_id.in_(train_ids)))
        connection.execute(
            insert(model_training_runs).values(
                run_id=training_id,
                dataset_version="supervised-core-v1",
                split_version="chronological-market-v1",
                feature_version="core-v1",
                label_version="official-outcome-v1",
                horizon_seconds=300,
                requested_start=start,
                requested_end=end,
                dataset_sha256="3" * 64,
                split_sha256="4" * 64,
                predictor_names=["pm_up_price"],
                dropped_all_missing=[],
                model_configs={"market_price": {"predictor": "pm_up_price", "missing_fallback": "training_prior", "clip_epsilon": 1e-6}},
                validation_champion="market_price",
                report={},
                artifact_manifest={},
                semantic_sha256="a" * 64,
                created_at=end,
            )
        )
        connection.execute(
            insert(calibration_edge_runs).values(
                run_id=run_id,
                calibration_version="platt-or-identity-v1",
                edge_policy_version="selected-ask-edge-v1",
                source_backtest_run_id="phase8-300-live-policy-postgres",
                source_backtest_semantic_sha256="b" * 64,
                source_training_run_id=training_id,
                source_training_semantic_sha256="a" * 64,
                dataset_version="supervised-core-v1",
                feature_version="core-v1",
                label_version="official-outcome-v1",
                horizon_seconds=300,
                requested_start=start,
                requested_end=end,
                dataset_sha256="c" * 64,
                config=config,
                config_sha256="d" * 64,
                source_plan_sha256="f" * 64,
                source_fold_membership_sha256=["1" * 64],
                report=report,
                semantic_sha256="9" * 64,
                created_at=end,
            )
        )
        for index, outcome in enumerate(("Down", "Down", "Down", "Up")):
            connection.execute(
                insert(market_labels).values(
                    condition_id=train_ids[index],
                    gamma_market_id=f"pg-gamma-{index}",
                    slug=f"pg-slug-{index}",
                    horizon_seconds=300,
                    market_start_at=start,
                    market_end_at=start.replace(hour=1),
                    official_outcome=outcome,
                    start_reference=None,
                    end_reference=None,
                    resolution_source="chainlink",
                    rules_hash="r" * 64,
                    label_source="polymarket_gamma_snapshot",
                    label_version="official-outcome-v1",
                    source_snapshot_sha256=str(index) * 64,
                    source_observed_at=start.replace(hour=1, minute=1),
                    generated_at=start.replace(hour=1, minute=2),
                )
            )
        spec = module.load_live_policy(connection, run_id)
        connection.execute(delete(calibration_edge_runs).where(calibration_edge_runs.c.run_id == run_id))
        connection.execute(delete(model_training_runs).where(model_training_runs.c.run_id == training_id))
        connection.execute(delete(market_labels).where(market_labels.c.condition_id.in_(train_ids)))

    assert spec.training_prior == 0.25
    assert spec.edge_policy == "no_trade"
    assert spec.selected_offset_seconds == 240
