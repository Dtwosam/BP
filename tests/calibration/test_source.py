from __future__ import annotations

import importlib
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, insert

from bp_engine.storage.schema import backtest_runs, metadata


def _module():
    return importlib.import_module("bp_engine.calibration.source")


def _stored_payload() -> dict[str, object]:
    start = datetime(2026, 8, 24, tzinfo=UTC)
    end = datetime(2026, 8, 25, tzinfo=UTC)
    fold_hash = "e" * 64
    final_hash = "f" * 64
    report = {
        "run_id": "phase8-300-source-test",
        "backtest_version": "walk-forward-v1",
        "source_training_run_id": "phase7-300-source",
        "source_training_semantic_sha256": "a" * 64,
        "dataset_version": "supervised-core-v1",
        "feature_version": "core-v1",
        "label_version": "official-outcome-v1",
        "horizon_seconds": 300,
        "start": start.isoformat().replace("+00:00", "Z"),
        "end": end.isoformat().replace("+00:00", "Z"),
        "dataset_sha256": "b" * 64,
        "config": {"train_duration_seconds": 28800.0},
        "config_sha256": "c" * 64,
        "plan_sha256": "d" * 64,
        "fold_membership_sha256": [fold_hash, final_hash],
        "folds": [
            {
                "index": 0,
                "membership_sha256": fold_hash,
                "train_condition_ids": ["train-a", "train-b"],
                "validation_condition_ids": ["val-a"],
                "test_condition_ids": ["test-a", "test-b"],
                "selected_offset_seconds": 240,
                "metrics": {"accuracy": 0.99},
                "execution": {"gross_execution_pnl_before_costs": 100.0},
            }
        ],
        "final_holdout": {
            "membership_sha256": final_hash,
            "train_condition_ids": ["final-train-a"],
            "validation_condition_ids": ["final-val-a"],
            "holdout_condition_ids": ["holdout-a", "holdout-b"],
            "selected_offset_seconds": 180,
            "metrics": {"accuracy": 0.01},
            "execution": {"gross_execution_pnl_before_costs": -100.0},
        },
        "semantic_sha256": "9" * 64,
    }
    return {
        "run_id": report["run_id"],
        "backtest_version": report["backtest_version"],
        "source_training_run_id": report["source_training_run_id"],
        "source_training_semantic_sha256": report[
            "source_training_semantic_sha256"
        ],
        "dataset_version": report["dataset_version"],
        "feature_version": report["feature_version"],
        "label_version": report["label_version"],
        "horizon_seconds": report["horizon_seconds"],
        "requested_start": start,
        "requested_end": end,
        "dataset_sha256": report["dataset_sha256"],
        "config": report["config"],
        "config_sha256": report["config_sha256"],
        "plan_sha256": report["plan_sha256"],
        "fold_membership_sha256": report["fold_membership_sha256"],
        "report": report,
        "semantic_sha256": report["semantic_sha256"],
        "created_at": end,
    }


def _load(mutator=None):
    module = _module()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    payload = _stored_payload()
    if mutator is not None:
        mutator(payload)
    with engine.begin() as connection:
        connection.execute(insert(backtest_runs).values(**payload))
        return module.load_backtest_source_spec(connection, str(payload["run_id"]))


def test_source_loader_omits_oos_metrics_and_preserves_frozen_offsets() -> None:
    spec = _load()

    assert spec.backtest_version == "walk-forward-v1"
    assert spec.folds[0].selected_offset_seconds == 240
    assert spec.final.selected_offset_seconds == 180
    assert spec.folds[0].test_condition_ids == ("test-a", "test-b")
    assert not hasattr(spec.folds[0], "metrics")
    assert not hasattr(spec.final, "metrics")
    assert not hasattr(spec, "aggregate_oos_metrics")


def test_wrong_backtest_version_is_rejected() -> None:
    def mutate(payload):
        payload["backtest_version"] = "future-v2"
        payload["report"]["backtest_version"] = "future-v2"

    with pytest.raises(ValueError, match="backtest_version"):
        _load(mutate)


def test_duplicate_ordinary_test_market_is_rejected() -> None:
    def mutate(payload):
        first = payload["report"]["folds"][0]
        payload["report"]["folds"].append(
            {
                **first,
                "index": 1,
                "membership_sha256": "7" * 64,
                "test_condition_ids": ["test-b", "test-c"],
            }
        )
        payload["report"]["fold_membership_sha256"].insert(1, "7" * 64)
        payload["fold_membership_sha256"].insert(1, "7" * 64)

    with pytest.raises(ValueError, match="ordinary test market reused"):
        _load(mutate)


def test_final_holdout_overlap_is_rejected() -> None:
    def mutate(payload):
        payload["report"]["final_holdout"]["holdout_condition_ids"] = ["test-a"]

    with pytest.raises(ValueError, match="final holdout overlaps"):
        _load(mutate)


def test_missing_selected_offset_is_rejected() -> None:
    def mutate(payload):
        del payload["report"]["folds"][0]["selected_offset_seconds"]

    with pytest.raises(ValueError, match="selected_offset_seconds"):
        _load(mutate)


def test_malformed_sha_is_rejected() -> None:
    def mutate(payload):
        payload["semantic_sha256"] = "not-a-sha"
        payload["report"]["semantic_sha256"] = "not-a-sha"

    with pytest.raises(ValueError, match="SHA-256"):
        _load(mutate)


def test_report_horizon_mismatch_is_rejected() -> None:
    def mutate(payload):
        payload["report"]["horizon_seconds"] = 900

    with pytest.raises(ValueError, match="horizon_seconds"):
        _load(mutate)
