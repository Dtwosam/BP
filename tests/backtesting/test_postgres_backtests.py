from __future__ import annotations

import os
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from bp_engine.backtesting.repository import BacktestRunConflict, BacktestRunRepository
from sqlalchemy import create_engine, delete, select

from bp_engine.storage.schema import backtest_runs, metadata

DATABASE_URL = os.getenv("BP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BP_TEST_DATABASE_URL is required for PostgreSQL integration coverage",
)


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
        run_id="phase8-postgres-run-registry",
        backtest_version="walk-forward-v1",
        source_training_run_id="phase7-postgres-source",
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
        fold_membership_sha256=("e" * 64,),
        report={"aggregate_oos": {"accuracy": 0.6}},
        semantic_sha256="9" * 64,
        created_at=created_at,
    )


def test_postgres_backtest_registry_is_idempotent_and_conflict_safe() -> None:
    assert DATABASE_URL is not None
    engine = create_engine(DATABASE_URL)
    metadata.create_all(engine)
    repository = BacktestRunRepository()
    created_at = datetime(2026, 8, 25, 20, 5, tzinfo=UTC)
    report = _report(created_at)

    with engine.begin() as connection:
        connection.execute(
            delete(backtest_runs).where(backtest_runs.c.run_id == report.run_id)
        )
        first = repository.store(connection, report)
        second = repository.store(
            connection,
            replace(report, created_at=created_at + timedelta(minutes=1)),
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
                    plan_sha256="1" * 64,
                    semantic_sha256="2" * 64,
                ),
            )
        connection.execute(
            delete(backtest_runs).where(backtest_runs.c.run_id == report.run_id)
        )

    assert first.created is True and first.existing is False
    assert second.created is False and second.existing is True
    assert stored_created_at == created_at
