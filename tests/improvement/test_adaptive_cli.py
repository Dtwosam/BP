from __future__ import annotations

import importlib
import json
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace


class _FakeEngine:
    def __init__(self) -> None:
        self.connection = object()
        self.disposed = False

    def begin(self):
        return nullcontext(self.connection)

    def dispose(self) -> None:
        self.disposed = True


def _modules():
    return (
        importlib.import_module("bp_engine.improvement.cli"),
        importlib.import_module("bp_engine.improvement.adaptive"),
    )


def _patch_database(monkeypatch, cli):
    engine = _FakeEngine()
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: SimpleNamespace(database_url="sqlite+pysqlite:///:memory:"),
    )
    monkeypatch.setattr(
        cli,
        "create_engine",
        lambda *_args, **_kwargs: engine,
    )
    return engine


def _readiness_args(*, bootstrap: datetime | None, cutoff: datetime) -> list[str]:
    args = [
        "adaptive-readiness",
        "--horizon-seconds",
        "300",
        "--feature-version",
        "core-v1",
        "--label-version",
        "official-outcome-v1",
        "--cutoff-at",
        cutoff.isoformat(),
    ]
    if bootstrap is not None:
        args.extend(["--bootstrap-since-at", bootstrap.isoformat()])
    return args


def _train_args(
    *, bootstrap: datetime | None, cutoff: datetime, training_start: datetime
) -> list[str]:
    args = _readiness_args(bootstrap=bootstrap, cutoff=cutoff)
    args[0] = "adaptive-train"
    args.extend(
        [
            "--training-start-at",
            training_start.isoformat(),
            "--output-dir",
            "adaptive-models",
            "--min-markets",
            "24",
        ]
    )
    return args


def test_adaptive_readiness_is_read_only(monkeypatch, capsys) -> None:
    cli, adaptive = _modules()
    engine = _patch_database(monkeypatch, cli)
    since = datetime(2026, 9, 12, 0, 0, tzinfo=UTC)
    cutoff = since + timedelta(hours=6)
    seen = {}

    class Repository:
        def latest_completed(self, connection, **kwargs):
            assert connection is engine.connection
            return None

        def store(self, *_args, **_kwargs):
            raise AssertionError("readiness must not store a cycle")

    def fake_readiness(connection, **kwargs):
        assert connection is engine.connection
        seen.update(kwargs)
        return SimpleNamespace(
            ready=True,
            eligible_resolved_market_count=50,
            trigger_count=50,
            semantic_sha256="a" * 64,
        )

    monkeypatch.setattr(adaptive, "AdaptiveLearningCycleRepository", Repository)
    monkeypatch.setattr(adaptive, "build_adaptive_readiness_report", fake_readiness)

    assert cli.main(_readiness_args(bootstrap=since, cutoff=cutoff)) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["command"] == "adaptive-readiness"
    assert payload["readiness"]["ready"] is True
    assert seen["since_at"] == since
    assert seen["cutoff_at"] == cutoff
    assert seen["trigger_count"] == 50
    assert engine.disposed is True


def test_adaptive_readiness_uses_previous_cycle_boundary(monkeypatch, capsys) -> None:
    cli, adaptive = _modules()
    engine = _patch_database(monkeypatch, cli)
    previous_cutoff = datetime(2026, 9, 12, 6, 0, tzinfo=UTC)
    cutoff = previous_cutoff + timedelta(hours=6)
    seen = {}

    class Repository:
        def latest_completed(self, connection, **kwargs):
            assert connection is engine.connection
            return {"cutoff_at": previous_cutoff}

    def fake_readiness(connection, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(
            ready=False,
            eligible_resolved_market_count=12,
            trigger_count=50,
            semantic_sha256="b" * 64,
        )

    monkeypatch.setattr(adaptive, "AdaptiveLearningCycleRepository", Repository)
    monkeypatch.setattr(adaptive, "build_adaptive_readiness_report", fake_readiness)

    assert cli.main(_readiness_args(bootstrap=None, cutoff=cutoff)) == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["readiness"]["ready"] is False
    assert seen["since_at"] == previous_cutoff


def test_adaptive_readiness_first_cycle_requires_bootstrap(monkeypatch, capsys) -> None:
    cli, adaptive = _modules()
    engine = _patch_database(monkeypatch, cli)
    cutoff = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)

    class Repository:
        def latest_completed(self, connection, **kwargs):
            assert connection is engine.connection
            return None

    monkeypatch.setattr(adaptive, "AdaptiveLearningCycleRepository", Repository)

    assert cli.main(_readiness_args(bootstrap=None, cutoff=cutoff)) == 2
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["error_type"] == "AdaptiveLearningError"
    assert "bootstrap_since_at" in payload["error"]


def test_adaptive_train_not_ready_is_structured(monkeypatch, capsys) -> None:
    cli, adaptive = _modules()
    engine = _patch_database(monkeypatch, cli)
    since = datetime(2026, 9, 12, 0, 0, tzinfo=UTC)
    cutoff = since + timedelta(hours=6)
    training_start = since - timedelta(days=7)

    def not_ready(connection, **kwargs):
        assert connection is engine.connection
        raise adaptive.AdaptiveLearningNotReadyError(
            "adaptive learning is not ready: 49 eligible resolved markets; requires 50"
        )

    monkeypatch.setattr(adaptive, "run_adaptive_training_cycle", not_ready)

    assert (
        cli.main(
            _train_args(
                bootstrap=since,
                cutoff=cutoff,
                training_start=training_start,
            )
        )
        == 2
    )
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["command"] == "adaptive-train"
    assert payload["error_type"] == "AdaptiveLearningNotReadyError"
    assert "49" in payload["error"]


def test_adaptive_train_emits_immutable_identities(monkeypatch, capsys) -> None:
    cli, adaptive = _modules()
    engine = _patch_database(monkeypatch, cli)
    since = datetime(2026, 9, 12, 0, 0, tzinfo=UTC)
    cutoff = since + timedelta(hours=6)
    training_start = since - timedelta(days=7)
    now = cutoff + timedelta(minutes=1)
    captured = {}
    monkeypatch.setattr(cli, "_utc_now", lambda: now)

    def fake_cycle(connection, **kwargs):
        assert connection is engine.connection
        captured.update(kwargs)
        return (
            SimpleNamespace(ready=True, semantic_sha256="a" * 64),
            SimpleNamespace(run_id="phase7-300-training", semantic_sha256="b" * 64),
            SimpleNamespace(cycle_id="adaptive-cycle-123", semantic_sha256="c" * 64),
        )

    monkeypatch.setattr(adaptive, "run_adaptive_training_cycle", fake_cycle)

    assert (
        cli.main(
            _train_args(
                bootstrap=since,
                cutoff=cutoff,
                training_start=training_start,
            )
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["readiness"]["ready"] is True
    assert payload["training"] == {
        "run_id": "phase7-300-training",
        "semantic_sha256": "b" * 64,
    }
    assert payload["cycle"] == {
        "cycle_id": "adaptive-cycle-123",
        "semantic_sha256": "c" * 64,
    }
    assert captured["output_dir"] == Path("adaptive-models")
    assert captured["trigger_count"] == 50
    assert captured["created_at"] == now


def test_adaptive_since_at_allows_bootstrap_when_cycle_table_is_absent() -> None:
    from sqlalchemy import create_engine, inspect

    cli, _ = _modules()
    bootstrap = datetime(2026, 9, 12, 17, 21, 13, tzinfo=UTC)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    args = SimpleNamespace(
        horizon_seconds=300,
        feature_version="core-v2-last-trade",
        label_version="official-outcome-v1",
        bootstrap_since_at=bootstrap.isoformat(),
    )

    with engine.begin() as connection:
        assert inspect(connection).has_table("adaptive_learning_cycles") is False
        assert cli._adaptive_since_at(connection, args) == bootstrap
        assert inspect(connection).has_table("adaptive_learning_cycles") is False


def test_adaptive_since_at_without_cycle_table_still_requires_bootstrap() -> None:
    import pytest
    from sqlalchemy import create_engine, inspect

    cli, adaptive = _modules()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    args = SimpleNamespace(
        horizon_seconds=300,
        feature_version="core-v2-last-trade",
        label_version="official-outcome-v1",
        bootstrap_since_at=None,
    )

    with engine.begin() as connection:
        assert inspect(connection).has_table("adaptive_learning_cycles") is False
        with pytest.raises(adaptive.AdaptiveLearningError, match="bootstrap_since_at"):
            cli._adaptive_since_at(connection, args)
        assert inspect(connection).has_table("adaptive_learning_cycles") is False
