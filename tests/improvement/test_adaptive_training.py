from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from bp_engine.storage.schema import metadata


def _adaptive():
    module = importlib.import_module("bp_engine.improvement.adaptive")
    if not hasattr(module, "run_adaptive_training_cycle"):
        pytest.fail("adaptive.run_adaptive_training_cycle must orchestrate research training")
    return module


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    return engine


class _Repository:
    def __init__(self, latest=None):
        self.latest = latest
        self.stored = []

    def latest_completed(self, connection, **kwargs):
        del connection, kwargs
        return self.latest

    def store(self, connection, cycle):
        del connection
        self.stored.append(cycle)
        return SimpleNamespace(created=True, existing=False)


def _readiness(adaptive, *, since_at, cutoff_at, ready=True, count=50):
    return adaptive.AdaptiveReadinessReport(
        readiness_version="adaptive-readiness-v1",
        horizon_seconds=300,
        feature_version="core-v1",
        label_version="official-outcome-v1",
        since_at=since_at,
        cutoff_at=cutoff_at,
        trigger_count=50,
        eligible_resolved_market_count=count,
        first_label_generated_at=since_at + timedelta(minutes=1) if count else None,
        last_label_generated_at=cutoff_at if count else None,
        condition_ids_sha256="a" * 64,
        ready=ready,
        semantic_sha256="b" * 64,
    )


def _training_report(*, start, end):
    return SimpleNamespace(
        run_id="phase7-300-training",
        semantic_sha256="c" * 64,
        dataset_sha256="d" * 64,
        split_sha256="e" * 64,
        validation_champion="logistic",
        best_test_result="logistic",
        boosted_promotion_eligible=False,
        evaluations={"logistic": {"validation": {"log_loss": 0.4}}},
        artifacts=({"family": "logistic", "sha256": "f" * 64},),
        gross_execution_diagnostic={"gross_execution_pnl_before_costs": 1.25},
        start=start,
        end=end,
    )


def _run(adaptive, connection, *, bootstrap_since_at, cutoff_at, training_start_at):
    return adaptive.run_adaptive_training_cycle(
        connection,
        horizon_seconds=300,
        feature_version="core-v1",
        label_version="official-outcome-v1",
        bootstrap_since_at=bootstrap_since_at,
        cutoff_at=cutoff_at,
        training_start_at=training_start_at,
        output_dir=Path("/tmp/adaptive-models"),
        min_markets=24,
        trigger_count=50,
        created_at=cutoff_at + timedelta(minutes=1),
    )


def test_first_cycle_requires_explicit_bootstrap_boundary(monkeypatch) -> None:
    adaptive = _adaptive()
    repo = _Repository(latest=None)
    monkeypatch.setattr(adaptive, "AdaptiveLearningCycleRepository", lambda: repo)
    cutoff = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)

    engine = _engine()
    with engine.begin() as connection, pytest.raises(
        adaptive.AdaptiveLearningError,
        match="bootstrap_since_at",
    ):
        _run(
            adaptive,
            connection,
            bootstrap_since_at=None,
            cutoff_at=cutoff,
            training_start_at=cutoff - timedelta(days=1),
        )


def test_existing_cycle_uses_previous_cutoff_and_rejects_boundary_reset(monkeypatch) -> None:
    adaptive = _adaptive()
    previous_cutoff = datetime(2026, 9, 12, 6, 0, tzinfo=UTC)
    repo = _Repository(latest={"cutoff_at": previous_cutoff})
    monkeypatch.setattr(adaptive, "AdaptiveLearningCycleRepository", lambda: repo)
    cutoff = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)

    engine = _engine()
    with engine.begin() as connection, pytest.raises(
        adaptive.AdaptiveLearningError,
        match="bootstrap_since_at",
    ):
        _run(
            adaptive,
            connection,
            bootstrap_since_at=previous_cutoff - timedelta(hours=1),
            cutoff_at=cutoff,
            training_start_at=previous_cutoff - timedelta(days=1),
        )


def test_not_ready_stops_before_training_or_cycle_store(monkeypatch) -> None:
    adaptive = _adaptive()
    since = datetime(2026, 9, 12, 0, 0, tzinfo=UTC)
    cutoff = since + timedelta(hours=6)
    repo = _Repository(latest=None)
    calls = []
    monkeypatch.setattr(adaptive, "AdaptiveLearningCycleRepository", lambda: repo)
    monkeypatch.setattr(
        adaptive,
        "build_adaptive_readiness_report",
        lambda *args, **kwargs: _readiness(
            adaptive,
            since_at=since,
            cutoff_at=cutoff,
            ready=False,
            count=49,
        ),
    )
    monkeypatch.setattr(adaptive, "train_horizon", lambda *args, **kwargs: calls.append(kwargs))

    engine = _engine()
    with engine.begin() as connection, pytest.raises(
        adaptive.AdaptiveLearningNotReadyError,
        match="49",
    ):
        _run(
            adaptive,
            connection,
            bootstrap_since_at=since,
            cutoff_at=cutoff,
            training_start_at=since - timedelta(days=1),
        )

    assert calls == []
    assert repo.stored == []


def test_ready_cycle_trains_accumulated_window_and_records_safety(monkeypatch) -> None:
    adaptive = _adaptive()
    since = datetime(2026, 9, 12, 0, 0, tzinfo=UTC)
    cutoff = since + timedelta(hours=6)
    training_start = since - timedelta(days=7)
    repo = _Repository(latest=None)
    training_calls = []

    monkeypatch.setattr(adaptive, "AdaptiveLearningCycleRepository", lambda: repo)
    monkeypatch.setattr(
        adaptive,
        "build_adaptive_readiness_report",
        lambda *args, **kwargs: _readiness(
            adaptive,
            since_at=since,
            cutoff_at=cutoff,
        ),
    )

    def fake_train(connection, **kwargs):
        del connection
        training_calls.append(kwargs)
        return _training_report(start=kwargs["start"], end=kwargs["end"])

    monkeypatch.setattr(adaptive, "train_horizon", fake_train)

    engine = _engine()
    with engine.begin() as connection:
        readiness, training, cycle = _run(
            adaptive,
            connection,
            bootstrap_since_at=since,
            cutoff_at=cutoff,
            training_start_at=training_start,
        )

    assert readiness.ready is True
    assert training.run_id == "phase7-300-training"
    assert training_calls == [
        {
            "start": training_start,
            "end": cutoff,
            "horizon_seconds": 300,
            "feature_version": "core-v1",
            "label_version": "official-outcome-v1",
            "label_generated_at_lte": cutoff,
            "output_dir": Path("/tmp/adaptive-models"),
            "min_markets": 24,
        }
    ]
    assert cycle.since_at == since
    assert cycle.cutoff_at == cutoff
    assert cycle.training_run_id == training.run_id
    assert cycle.summary["automatic_promotion"] is False
    assert cycle.summary["final_holdout_accessed"] is False
    assert cycle.summary["paper_model_activated"] is False
    assert cycle.summary["live_trading_enabled"] is False
    assert cycle.summary["dataset_sha256"] == "d" * 64
    assert cycle.summary["split_sha256"] == "e" * 64
    assert len(repo.stored) == 1
    assert repo.stored[0].cycle_id == cycle.cycle_id


def test_subsequent_cycle_derives_since_at_from_latest_cycle(monkeypatch) -> None:
    adaptive = _adaptive()
    previous_cutoff = datetime(2026, 9, 12, 6, 0, tzinfo=UTC)
    cutoff = previous_cutoff + timedelta(hours=6)
    training_start = previous_cutoff - timedelta(days=7)
    repo = _Repository(latest={"cutoff_at": previous_cutoff})
    seen = {}
    monkeypatch.setattr(adaptive, "AdaptiveLearningCycleRepository", lambda: repo)

    def fake_readiness(connection, **kwargs):
        del connection
        seen.update(kwargs)
        return _readiness(
            adaptive,
            since_at=kwargs["since_at"],
            cutoff_at=kwargs["cutoff_at"],
        )

    monkeypatch.setattr(adaptive, "build_adaptive_readiness_report", fake_readiness)
    monkeypatch.setattr(
        adaptive,
        "train_horizon",
        lambda connection, **kwargs: _training_report(
            start=kwargs["start"],
            end=kwargs["end"],
        ),
    )

    engine = _engine()
    with engine.begin() as connection:
        readiness, _, _ = _run(
            adaptive,
            connection,
            bootstrap_since_at=None,
            cutoff_at=cutoff,
            training_start_at=training_start,
        )

    assert readiness.since_at == previous_cutoff
    assert seen["since_at"] == previous_cutoff


def test_training_start_must_precede_cutoff(monkeypatch) -> None:
    adaptive = _adaptive()
    since = datetime(2026, 9, 12, 0, 0, tzinfo=UTC)
    cutoff = since + timedelta(hours=6)
    repo = _Repository(latest=None)
    monkeypatch.setattr(adaptive, "AdaptiveLearningCycleRepository", lambda: repo)
    monkeypatch.setattr(
        adaptive,
        "build_adaptive_readiness_report",
        lambda *args, **kwargs: _readiness(
            adaptive,
            since_at=since,
            cutoff_at=cutoff,
        ),
    )

    engine = _engine()
    with engine.begin() as connection, pytest.raises(ValueError, match="training_start_at"):
        _run(
            adaptive,
            connection,
            bootstrap_since_at=since,
            cutoff_at=cutoff,
            training_start_at=cutoff,
        )
