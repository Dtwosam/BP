from __future__ import annotations

import importlib
import os
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, delete, select

from bp_engine.storage import schema

DATABASE_URL = os.getenv("BP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BP_TEST_DATABASE_URL is required for PostgreSQL integration coverage",
)


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
        run_id="phase9-300-registry-postgres",
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
        config={"fee_rate": 0.07, "slippage_buffer": 0.01},
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


def test_postgres_registry_is_idempotent_and_keeps_original_created_at() -> None:
    assert DATABASE_URL is not None
    module = importlib.import_module("bp_engine.calibration.repository")
    table = schema.calibration_edge_runs
    engine = create_engine(DATABASE_URL)
    schema.metadata.create_all(engine)
    repository = module.CalibrationEdgeRunRepository()
    created_at = datetime(2026, 8, 26, 0, 30, tzinfo=UTC)
    report = _report(created_at)

    with engine.begin() as connection:
        connection.execute(delete(table).where(table.c.run_id == report.run_id))
        first = repository.store(connection, report)
        second = repository.store(
            connection,
            replace(report, created_at=created_at + timedelta(minutes=10)),
        )
        stored = connection.execute(
            select(table).where(table.c.run_id == report.run_id)
        ).mappings().one()
        connection.execute(delete(table).where(table.c.run_id == report.run_id))

    assert first.created is True and first.existing is False
    assert second.created is False and second.existing is True
    assert stored["semantic_sha256"] == report.semantic_sha256
    assert stored["source_backtest_run_id"] == report.source_backtest_run_id
    assert stored["created_at"] == created_at
