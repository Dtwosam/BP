from __future__ import annotations

import importlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine

from bp_engine.storage.schema import metadata


def _components():
    adaptive = importlib.import_module("bp_engine.improvement.adaptive")
    try:
        repository = importlib.import_module("bp_engine.improvement.adaptive_repository")
    except ModuleNotFoundError:
        pytest.fail("adaptive_repository must implement the adaptive cycle ledger")
    if not hasattr(adaptive, "AdaptiveLearningCycle"):
        pytest.fail("adaptive.AdaptiveLearningCycle must define immutable cycle evidence")
    return adaptive, repository


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    return engine


def _cycle(
    adaptive,
    *,
    cutoff_at: datetime,
    feature_version: str = "core-v1",
    label_version: str = "official-outcome-v1",
    horizon_seconds: int = 300,
    eligible_count: int = 50,
):
    since_at = cutoff_at - timedelta(hours=6)
    return adaptive.AdaptiveLearningCycle.build(
        horizon_seconds=horizon_seconds,
        feature_version=feature_version,
        label_version=label_version,
        trigger_count=50,
        since_at=since_at,
        cutoff_at=cutoff_at,
        eligible_resolved_market_count=eligible_count,
        readiness_semantic_sha256="a" * 64,
        training_start_at=cutoff_at - timedelta(hours=5),
        training_run_id=f"training-{feature_version}-{cutoff_at.isoformat()}",
        training_semantic_sha256="b" * 64,
        summary={
            "automatic_promotion": False,
            "final_holdout_accessed": False,
            "paper_model_activated": False,
        },
        created_at=cutoff_at + timedelta(minutes=1),
    )


def test_cycle_store_is_append_only_and_idempotent() -> None:
    adaptive, repository_module = _components()
    cutoff = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
    cycle = _cycle(adaptive, cutoff_at=cutoff)
    engine = _engine()
    repo = repository_module.AdaptiveLearningCycleRepository()

    with engine.begin() as connection:
        first = repo.store(connection, cycle)
        second = repo.store(connection, cycle)
        stored = repo.get(connection, cycle.cycle_id)

    assert first.created is True
    assert first.existing is False
    assert second.created is False
    assert second.existing is True
    assert stored is not None
    assert stored["cycle_id"] == cycle.cycle_id
    assert stored["semantic_sha256"] == cycle.semantic_sha256
    assert stored["summary"] == cycle.summary


def test_cycle_store_rejects_tampered_semantics_for_same_immutable_id() -> None:
    adaptive, repository_module = _components()
    cutoff = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
    cycle = _cycle(adaptive, cutoff_at=cutoff)
    tampered = replace(cycle, eligible_resolved_market_count=51)
    engine = _engine()
    repo = repository_module.AdaptiveLearningCycleRepository()

    with engine.begin() as connection:
        repo.store(connection, cycle)
        with pytest.raises(repository_module.AdaptiveLearningCycleConflict):
            repo.store(connection, tampered)


def test_latest_completed_is_scoped_to_exact_learning_stream() -> None:
    adaptive, repository_module = _components()
    base = datetime(2026, 9, 12, 6, 0, tzinfo=UTC)
    core_old = _cycle(adaptive, cutoff_at=base, feature_version="core-v1")
    core_new = _cycle(
        adaptive,
        cutoff_at=base + timedelta(hours=6),
        feature_version="core-v1",
    )
    other_feature = _cycle(
        adaptive,
        cutoff_at=base + timedelta(hours=8),
        feature_version="core-v2-last-trade",
    )
    other_label = _cycle(
        adaptive,
        cutoff_at=base + timedelta(hours=9),
        feature_version="core-v1",
        label_version="other-label",
    )
    engine = _engine()
    repo = repository_module.AdaptiveLearningCycleRepository()

    with engine.begin() as connection:
        for cycle in (core_old, other_feature, core_new, other_label):
            repo.store(connection, cycle)
        latest = repo.latest_completed(
            connection,
            horizon_seconds=300,
            feature_version="core-v1",
            label_version="official-outcome-v1",
        )
        unrelated = repo.latest_completed(
            connection,
            horizon_seconds=300,
            feature_version="missing-feature",
            label_version="official-outcome-v1",
        )

    assert latest is not None
    assert latest["cycle_id"] == core_new.cycle_id
    assert latest["cutoff_at"].replace(tzinfo=UTC) == core_new.cutoff_at
    assert unrelated is None


def test_cycle_build_rejects_invalid_learning_boundaries() -> None:
    adaptive, _ = _components()
    cutoff = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="eligible_resolved_market_count"):
        adaptive.AdaptiveLearningCycle.build(
            horizon_seconds=300,
            feature_version="core-v1",
            label_version="official-outcome-v1",
            trigger_count=50,
            since_at=cutoff - timedelta(hours=1),
            cutoff_at=cutoff,
            eligible_resolved_market_count=49,
            readiness_semantic_sha256="a" * 64,
            training_start_at=cutoff - timedelta(minutes=30),
            training_run_id="training-run",
            training_semantic_sha256="b" * 64,
            summary={
                "automatic_promotion": False,
                "final_holdout_accessed": False,
            },
            created_at=cutoff + timedelta(minutes=1),
        )


def test_adaptive_cycle_table_is_exposed_through_storage_schema() -> None:
    _components()
    from bp_engine.storage import schema

    assert hasattr(schema, "adaptive_learning_cycles")
    table = schema.adaptive_learning_cycles
    assert table.name == "adaptive_learning_cycles"
    assert table.c.cycle_id.unique is True
