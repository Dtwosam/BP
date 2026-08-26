from __future__ import annotations

import importlib
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, insert

from bp_engine.storage.schema import (
    calibration_edge_runs,
    market_labels,
    metadata,
    model_training_runs,
)

START = datetime(2026, 8, 24, tzinfo=UTC)
END = datetime(2026, 8, 25, tzinfo=UTC)
RUN_ID = "phase9-300-live-policy-test"
SOURCE_TRAINING_RUN_ID = "phase7-300-live-policy-test"


def _calibration_report(*, holdout_accuracy: float = 0.5, policy: str = "trade_threshold") -> dict[str, object]:
    min_edge = 0.05 if policy == "trade_threshold" else None
    return {
        "run_id": RUN_ID,
        "calibration_version": "platt-or-identity-v1",
        "edge_policy_version": "selected-ask-edge-v1",
        "source_backtest_run_id": "phase8-300-live-policy-test",
        "source_backtest_version": "walk-forward-v1",
        "source_backtest_semantic_sha256": "b" * 64,
        "source_training_run_id": SOURCE_TRAINING_RUN_ID,
        "source_training_semantic_sha256": "a" * 64,
        "dataset_version": "supervised-core-v1",
        "feature_version": "core-v1",
        "label_version": "official-outcome-v1",
        "horizon_seconds": 300,
        "start": "2026-08-24T00:00:00Z",
        "end": "2026-08-25T00:00:00Z",
        "dataset_sha256": "c" * 64,
        "config": {
            "fee_rate": 0.07,
            "slippage_buffer": 0.01,
            "min_edge_grid": [0.0, 0.01, 0.02, 0.05],
            "min_validation_trades": 3,
            "max_spread": None,
        },
        "config_sha256": "d" * 64,
        "source_backtest_config_sha256": "e" * 64,
        "source_plan_sha256": "f" * 64,
        "source_fold_membership_sha256": ["1" * 64, "2" * 64],
        "folds": [],
        "aggregate_oos": {"metrics": {"accuracy": 0.9}},
        "final_holdout": {
            "membership_sha256": "2" * 64,
            "train_condition_ids": ["train-0", "train-1", "train-2", "train-3"],
            "validation_condition_ids": ["val-0", "val-1"],
            "holdout_condition_ids": ["holdout-0", "holdout-1"],
            "selected_offset_seconds": 240,
            "calibration_selection_fit_partition": "train",
            "calibration_selection_partition": "validation",
            "edge_selection_partition": "validation",
            "evaluation_partition": "holdout",
            "calibration_selection": {
                "method": "identity",
                "fit": {
                    "method": "identity",
                    "intercept": None,
                    "coefficient": None,
                },
                "validation_metrics": {"accuracy": 1.0},
                "candidates": [],
            },
            "edge_policy_selection": {
                "policy": policy,
                "min_edge": min_edge,
                "validation_metrics": {"trade_count": 3},
                "candidates": [],
            },
            "raw_metrics": {"accuracy": holdout_accuracy},
            "calibrated_metrics": {"accuracy": holdout_accuracy},
            "edge_metrics": {"realized_pnl_after_assumed_costs": -99.0},
            "predictions": [
                {
                    "condition_id": "holdout-0",
                    "target": 1,
                    "raw_probability": 0.99,
                }
            ],
        },
        "semantic_sha256": "9" * 64,
    }


def _seed(connection, *, holdout_accuracy: float = 0.5, policy: str = "trade_threshold") -> None:
    connection.execute(
        insert(model_training_runs).values(
            run_id=SOURCE_TRAINING_RUN_ID,
            dataset_version="supervised-core-v1",
            split_version="chronological-market-v1",
            feature_version="core-v1",
            label_version="official-outcome-v1",
            horizon_seconds=300,
            requested_start=START,
            requested_end=END,
            dataset_sha256="3" * 64,
            split_sha256="4" * 64,
            predictor_names=["pm_up_price"],
            dropped_all_missing=[],
            model_configs={
                "market_price": {
                    "predictor": "pm_up_price",
                    "missing_fallback": "training_prior",
                    "clip_epsilon": 1e-6,
                }
            },
            validation_champion="market_price",
            report={},
            artifact_manifest={},
            semantic_sha256="a" * 64,
            created_at=END,
        )
    )
    report = _calibration_report(holdout_accuracy=holdout_accuracy, policy=policy)
    connection.execute(
        insert(calibration_edge_runs).values(
            run_id=RUN_ID,
            calibration_version="platt-or-identity-v1",
            edge_policy_version="selected-ask-edge-v1",
            source_backtest_run_id="phase8-300-live-policy-test",
            source_backtest_semantic_sha256="b" * 64,
            source_training_run_id=SOURCE_TRAINING_RUN_ID,
            source_training_semantic_sha256="a" * 64,
            dataset_version="supervised-core-v1",
            feature_version="core-v1",
            label_version="official-outcome-v1",
            horizon_seconds=300,
            requested_start=START,
            requested_end=END,
            dataset_sha256="c" * 64,
            config=report["config"],
            config_sha256="d" * 64,
            source_plan_sha256="f" * 64,
            source_fold_membership_sha256=["1" * 64, "2" * 64],
            report=report,
            semantic_sha256="9" * 64,
            created_at=END,
        )
    )
    for index, outcome in enumerate(("Down", "Down", "Up", "Up")):
        connection.execute(
            insert(market_labels).values(
                condition_id=f"train-{index}",
                gamma_market_id=f"gamma-{index}",
                slug=f"slug-{index}",
                horizon_seconds=300,
                market_start_at=START,
                market_end_at=START.replace(hour=1),
                official_outcome=outcome,
                start_reference=None,
                end_reference=None,
                resolution_source="chainlink",
                rules_hash="r" * 64,
                label_source="polymarket_gamma_snapshot",
                label_version="official-outcome-v1",
                source_snapshot_sha256=str(index) * 64,
                source_observed_at=START.replace(hour=1, minute=1),
                generated_at=START.replace(hour=1, minute=2),
            )
        )


def _connection():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    return engine


def test_load_live_policy_exposes_only_frozen_selection_context() -> None:
    module = importlib.import_module("bp_engine.live_prediction.policy")
    engine = _connection()
    with engine.begin() as connection:
        _seed(connection)
        spec = module.load_live_policy(connection, RUN_ID)

    assert spec.source_calibration_run_id == RUN_ID
    assert spec.calibration_version == "platt-or-identity-v1"
    assert spec.edge_policy_version == "selected-ask-edge-v1"
    assert spec.source_feature_version == "core-v1"
    assert spec.horizon_seconds == 300
    assert spec.selected_offset_seconds == 240
    assert spec.calibration_fit.method == "identity"
    assert spec.edge_policy == "trade_threshold"
    assert spec.min_edge == 0.05
    assert spec.training_prior == 0.5
    assert not hasattr(spec, "holdout_condition_ids")
    assert not hasattr(spec, "final_holdout_metrics")
    assert not hasattr(spec, "predictions")


def test_holdout_performance_does_not_become_policy_input() -> None:
    module = importlib.import_module("bp_engine.live_prediction.policy")
    left_engine = _connection()
    right_engine = _connection()
    with left_engine.begin() as connection:
        _seed(connection, holdout_accuracy=0.0)
        left = module.load_live_policy(connection, RUN_ID)
    with right_engine.begin() as connection:
        _seed(connection, holdout_accuracy=1.0)
        right = module.load_live_policy(connection, RUN_ID)

    assert left.selected_offset_seconds == right.selected_offset_seconds
    assert left.calibration_fit == right.calibration_fit
    assert left.edge_policy == right.edge_policy
    assert left.min_edge == right.min_edge
    assert left.training_prior == right.training_prior


def test_no_trade_policy_is_preserved() -> None:
    module = importlib.import_module("bp_engine.live_prediction.policy")
    engine = _connection()
    with engine.begin() as connection:
        _seed(connection, policy="no_trade")
        spec = module.load_live_policy(connection, RUN_ID)

    assert spec.edge_policy == "no_trade"
    assert spec.min_edge is None


def test_wrong_phase9_version_fails_closed() -> None:
    module = importlib.import_module("bp_engine.live_prediction.policy")
    engine = _connection()
    with engine.begin() as connection:
        _seed(connection)
        connection.exec_driver_sql(
            "UPDATE calibration_edge_runs SET calibration_version='wrong-v1' WHERE run_id=?",
            (RUN_ID,),
        )
        with pytest.raises(module.LivePolicyIntegrityError, match="calibration_version"):
            module.load_live_policy(connection, RUN_ID)


def test_wrong_training_model_contract_fails_closed() -> None:
    module = importlib.import_module("bp_engine.live_prediction.policy")
    engine = _connection()
    with engine.begin() as connection:
        _seed(connection)
        connection.exec_driver_sql(
            "UPDATE model_training_runs SET validation_champion='xgb' WHERE run_id=?",
            (SOURCE_TRAINING_RUN_ID,),
        )
        with pytest.raises(module.LivePolicyIntegrityError, match="training model"):
            module.load_live_policy(connection, RUN_ID)


def test_validation_selection_marker_mismatch_fails_closed() -> None:
    module = importlib.import_module("bp_engine.live_prediction.policy")
    engine = _connection()
    with engine.begin() as connection:
        _seed(connection)
        row = connection.execute(calibration_edge_runs.select()).mappings().one()
        report = dict(row["report"])
        final = dict(report["final_holdout"])
        final["edge_selection_partition"] = "holdout"
        report["final_holdout"] = final
        connection.execute(
            calibration_edge_runs.update().values(report=report)
        )
        with pytest.raises(module.LivePolicyIntegrityError, match="edge selection"):
            module.load_live_policy(connection, RUN_ID)
