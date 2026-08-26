from __future__ import annotations

import importlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, select

from bp_engine.storage import schema


@dataclass(frozen=True)
class _RegistryReport:
    run_id: str
    calibration_version: str
    edge_policy_version: str
    source_backtest_run_id: str
    source_backtest_version: str
    source_backtest_semantic_sha256: str
    source_training_run_id: str
    source_training_semantic_sha256: str
    dataset_version: str
    feature_version: str
    label_version: str
    horizon_seconds: int
    start: datetime
    end: datetime
    dataset_sha256: str
    config: dict[str, Any]
    config_sha256: str
    source_backtest_config_sha256: str
    source_plan_sha256: str
    source_fold_membership_sha256: tuple[str, ...]
    folds: tuple[dict[str, Any], ...]
    aggregate_oos: dict[str, Any]
    final_holdout: dict[str, Any]
    semantic_sha256: str
    created_at: datetime


def _report(created_at: datetime) -> _RegistryReport:
    return _RegistryReport(
        run_id="phase9-300-registry-test",
        calibration_version="platt-or-identity-v1",
        edge_policy_version="selected-ask-edge-v1",
        source_backtest_run_id="phase8-300-source",
        source_backtest_version="walk-forward-v1",
        source_backtest_semantic_sha256="9" * 64,
        source_training_run_id="phase7-300-source",
        source_training_semantic_sha256="a" * 64,
        dataset_version="supervised-core-v1",
        feature_version="core-v1",
        label_version="official-outcome-v1",
        horizon_seconds=300,
        start=datetime(2026, 8, 24, tzinfo=UTC),
        end=datetime(2026, 8, 25, tzinfo=UTC),
        dataset_sha256="b" * 64,
        config={
            "fee_rate": 0.07,
            "slippage_buffer": 0.01,
            "min_edge_grid": [0.0, 0.01, 0.02],
            "min_validation_trades": 3,
            "max_spread": None,
        },
        config_sha256="c" * 64,
        source_backtest_config_sha256="4" * 64,
        source_plan_sha256="d" * 64,
        source_fold_membership_sha256=("e" * 64, "f" * 64),
        folds=({"index": 0, "edge_policy": "no_trade"},),
        aggregate_oos={"trade_count": 0},
        final_holdout={"trade_count": 0},
        semantic_sha256="8" * 64,
        created_at=created_at,
    )


def _repository_module():
    return importlib.import_module("bp_engine.calibration.repository")


def _table():
    return getattr(schema, "calibration_edge_runs")


def test_calibration_edge_schema_has_immutable_identity_and_hashes() -> None:
    table = _table()
    expected = {
        "run_id",
        "calibration_version",
        "edge_policy_version",
        "source_backtest_run_id",
        "source_backtest_semantic_sha256",
        "source_training_run_id",
        "source_training_semantic_sha256",
        "dataset_version",
        "feature_version",
        "label_version",
        "horizon_seconds",
        "requested_start",
        "requested_end",
        "dataset_sha256",
        "config",
        "config_sha256",
        "source_plan_sha256",
        "source_fold_membership_sha256",
        "report",
        "semantic_sha256",
        "created_at",
    }
    assert expected <= set(table.c.keys())
    unique_sets = {
        tuple(constraint.columns.keys())
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("run_id",) in unique_sets


def test_calibration_edge_migration_declares_additive_registry() -> None:
    migration = (
        Path(__file__).parents[2] / "migrations" / "0009_calibration_edge_runs.sql"
    )
    text = migration.read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS calibration_edge_runs" in text
    assert "run_id VARCHAR(128) NOT NULL" in text
    assert "report JSONB NOT NULL" in text
    assert "source_fold_membership_sha256 JSONB NOT NULL" in text
    assert (
        "UNIQUE (run_id)" in text
        or "uq_calibration_edge_runs_run_id UNIQUE (run_id)" in text
    )


def test_repository_is_idempotent_conflict_safe_and_preserves_created_at() -> None:
    module = _repository_module()
    table = _table()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    schema.metadata.create_all(engine)
    repository = module.CalibrationEdgeRunRepository()
    created_at = datetime(2026, 8, 26, 0, 30, tzinfo=UTC)
    report = _report(created_at)

    with engine.begin() as connection:
        first = repository.store(connection, report)
        second = repository.store(
            connection,
            replace(report, created_at=created_at + timedelta(minutes=5)),
        )
        stored_created_at = connection.execute(
            select(table.c.created_at).where(table.c.run_id == report.run_id)
        ).scalar_one()
        with pytest.raises(module.CalibrationEdgeRunConflict, match="run_id"):
            repository.store(
                connection,
                replace(report, semantic_sha256="7" * 64),
            )

    assert first.created is True and first.existing is False
    assert second.created is False and second.existing is True
    assert stored_created_at.replace(tzinfo=UTC) == created_at


def test_repository_rejects_invalid_report_digests() -> None:
    module = _repository_module()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    schema.metadata.create_all(engine)
    repository = module.CalibrationEdgeRunRepository()

    with engine.begin() as connection:
        with pytest.raises(ValueError, match="semantic_sha256"):
            repository.store(
                connection,
                replace(
                    _report(datetime(2026, 8, 26, tzinfo=UTC)),
                    semantic_sha256="short",
                ),
            )
