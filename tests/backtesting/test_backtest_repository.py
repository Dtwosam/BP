from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from bp_engine.backtesting.repository import BacktestRunConflict, BacktestRunRepository
from sqlalchemy import create_engine, select

from bp_engine.storage.schema import backtest_runs, metadata


@dataclass(frozen=True)
class _RegistryReport:
    run_id: str
    backtest_version: str
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
    plan_sha256: str
    fold_membership_sha256: tuple[str, ...]
    report: dict[str, Any]
    semantic_sha256: str
    created_at: datetime


def _report(created_at: datetime) -> _RegistryReport:
    return _RegistryReport(
        run_id="phase8-300-registry-test",
        backtest_version="walk-forward-v1",
        source_training_run_id="phase7-300-source",
        source_training_semantic_sha256="a" * 64,
        dataset_version="supervised-core-v1",
        feature_version="core-v1",
        label_version="official-outcome-v1",
        horizon_seconds=300,
        start=datetime(2026, 8, 24, tzinfo=UTC),
        end=datetime(2026, 8, 25, tzinfo=UTC),
        dataset_sha256="b" * 64,
        config={"train_hours": 8, "test_hours": 2},
        config_sha256="c" * 64,
        plan_sha256="d" * 64,
        fold_membership_sha256=("e" * 64, "f" * 64),
        report={"aggregate_oos": {"accuracy": 0.6}, "final_holdout": {"accuracy": 0.5}},
        semantic_sha256="9" * 64,
        created_at=created_at,
    )


def test_backtest_run_schema_has_immutable_identity_and_hashes() -> None:
    expected = {
        "run_id",
        "backtest_version",
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
        "plan_sha256",
        "fold_membership_sha256",
        "report",
        "semantic_sha256",
        "created_at",
    }
    assert expected <= set(backtest_runs.c.keys())
    unique_sets = {
        tuple(constraint.columns.keys())
        for constraint in backtest_runs.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("run_id",) in unique_sets


def test_backtest_migration_declares_additive_registry() -> None:
    migration = Path(__file__).parents[2] / "migrations" / "0008_backtest_runs.sql"
    text = migration.read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS backtest_runs" in text
    assert "run_id VARCHAR(128) NOT NULL" in text
    assert "report JSONB NOT NULL" in text
    assert "UNIQUE (run_id)" in text or "uq_backtest_runs_run_id UNIQUE (run_id)" in text


def test_backtest_repository_is_idempotent_and_conflict_safe() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    repository = BacktestRunRepository()
    created_at = datetime(2026, 8, 25, 20, 0, tzinfo=UTC)
    report = _report(created_at)

    with engine.begin() as connection:
        first = repository.store(connection, report)
        second = repository.store(
            connection,
            replace(report, created_at=created_at + timedelta(minutes=5)),
        )
        stored_created_at = connection.execute(
            select(backtest_runs.c.created_at).where(
                backtest_runs.c.run_id == report.run_id
            )
        ).scalar_one()
        with pytest.raises(BacktestRunConflict, match="run_id"):
            repository.store(
                connection,
                replace(
                    report,
                    config_sha256="1" * 64,
                    semantic_sha256="2" * 64,
                ),
            )

    assert first.created is True and first.existing is False
    assert second.created is False and second.existing is True
    assert stored_created_at.replace(tzinfo=UTC) == created_at
