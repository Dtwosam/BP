from __future__ import annotations

import importlib
import json
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from bp_engine.improvement.models import ImprovementExperimentSpec, PromotionDecision
from bp_engine.improvement.repository import ImprovementStoreResult


class _FakeEngine:
    def __init__(self) -> None:
        self.connection = object()
        self.disposed = False

    def begin(self):
        return nullcontext(self.connection)

    def dispose(self) -> None:
        self.disposed = True


def _module():
    return importlib.import_module("bp_engine.improvement.cli")


def _spec_payload() -> dict[str, object]:
    return {
        "experiment_version": "improvement-experiment-v1",
        "hypothesis": "A spread guard improves executable economics.",
        "horizon_seconds": 300,
        "change_family": "abstention",
        "champion": {
            "calibration_run_id": "phase9-300-c9f0e00eb7836af08008c66909f8f179",
            "calibration_semantic_sha256": "a" * 64,
            "backtest_run_id": "phase8-300-example",
            "backtest_semantic_sha256": "b" * 64,
            "training_run_id": "phase7-300-example",
            "training_semantic_sha256": "c" * 64,
        },
        "challenger": {"kind": "spread_guard_v1", "grid": [0.02, 0.04, None]},
        "source_versions": {
            "dataset": "supervised-core-v1",
            "feature": "core-v1",
            "label": "official-outcome-v1",
        },
        "research_start": "2026-08-24T00:00:00Z",
        "research_end": "2026-08-25T00:00:00Z",
        "selection_policy": {"allowed_roles": ["development_validation"]},
        "confirmation_policy": {
            "allowed_roles": ["fresh_holdout", "prospective_paper"]
        },
        "cost_assumptions": {"fee_rate": 0.07, "slippage_buffer": 0.01},
        "primary_metric": "net_pnl_delta",
        "guardrail_metrics": ["calibrated_log_loss", "calibrated_brier"],
        "legacy_confirmation_identifiers": ["legacy-final-1"],
        "created_at": "2026-08-29T08:00:00Z",
    }


def _patch_database(monkeypatch, cli):
    engine = _FakeEngine()
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: SimpleNamespace(database_url="postgresql+psycopg://unit-test/bp"),
    )
    seen: list[str] = []

    def fake_create_engine(url: str, **kwargs):
        seen.append(url)
        assert kwargs == {"pool_pre_ping": True}
        return engine

    monkeypatch.setattr(cli, "create_engine", fake_create_engine)
    return engine, seen


def test_help_states_research_paper_only(capsys) -> None:
    cli = _module()

    try:
        cli.main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0

    output = capsys.readouterr().out.lower()
    assert "research/paper only" in output


def test_register_builds_frozen_spec_and_emits_one_json_object(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    cli = _module()
    engine, seen_urls = _patch_database(monkeypatch, cli)
    spec_path = tmp_path / "experiment.json"
    spec_path.write_text(json.dumps(_spec_payload()), encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_register(connection, spec):
        captured["connection"] = connection
        captured["spec"] = spec
        return ImprovementStoreResult(created=True, existing=False)

    monkeypatch.setattr(cli.service, "register_experiment", fake_register)

    assert cli.main(["register", "--spec", str(spec_path)]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["ok"] is True
    assert payload["command"] == "register"
    assert payload["created"] is True
    assert payload["existing"] is False
    assert isinstance(captured["spec"], ImprovementExperimentSpec)
    assert payload["experiment_id"] == captured["spec"].experiment_id
    assert captured["connection"] is engine.connection
    assert seen_urls == ["postgresql+psycopg://unit-test/bp"]
    assert engine.disposed is True


def test_report_emits_json_safe_append_only_history(monkeypatch, capsys) -> None:
    cli = _module()
    engine, _ = _patch_database(monkeypatch, cli)
    created_at = datetime(2026, 8, 29, 8, 0, tzinfo=UTC)

    def fake_report(connection, experiment_id):
        assert connection is engine.connection
        assert experiment_id == "phase13-exp-123"
        return {
            "experiment": {"experiment_id": experiment_id, "created_at": created_at},
            "evaluations": [],
            "decisions": [],
        }

    monkeypatch.setattr(cli.service, "get_experiment_report", fake_report)

    assert cli.main(["report", "--experiment-id", "phase13-exp-123"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload == {
        "command": "report",
        "ok": True,
        "report": {
            "decisions": [],
            "evaluations": [],
            "experiment": {
                "created_at": "2026-08-29T08:00:00Z",
                "experiment_id": "phase13-exp-123",
            },
        },
    }
    assert engine.disposed is True


def test_decide_passes_enum_rationale_and_deterministic_timestamp(monkeypatch, capsys) -> None:
    cli = _module()
    engine, _ = _patch_database(monkeypatch, cli)
    now = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
    monkeypatch.setattr(cli, "_utc_now", lambda: now)
    captured: dict[str, object] = {}

    def fake_decide(connection, **kwargs):
        captured["connection"] = connection
        captured.update(kwargs)
        return SimpleNamespace(
            decision_id="phase13-decision-abc",
            evaluation_id=kwargs["evaluation_id"],
            experiment_id="phase13-exp-123",
            decision=kwargs["decision"],
            rationale=kwargs["rationale"],
            created_at=kwargs["created_at"],
        )

    monkeypatch.setattr(cli.service, "record_decision", fake_decide)

    assert (
        cli.main(
            [
                "decide",
                "--evaluation-id",
                "phase13-eval-123",
                "--decision",
                "keep_champion",
                "--rationale",
                "Independent confirmation remains inconclusive.",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is True
    assert payload["command"] == "decide"
    assert payload["decision"]["decision"] == "keep_champion"
    assert captured["connection"] is engine.connection
    assert captured["evaluation_id"] == "phase13-eval-123"
    assert captured["decision"] is PromotionDecision.KEEP_CHAMPION
    assert captured["rationale"] == "Independent confirmation remains inconclusive."
    assert captured["created_at"] == now
    assert engine.disposed is True


def test_evaluate_fails_explicitly_before_challenger_adapter(capsys) -> None:
    cli = _module()

    assert cli.main(["evaluate", "--experiment-id", "phase13-exp-123"]) != 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["ok"] is False
    assert payload["command"] == "evaluate"
    assert "challenger adapter not installed" in payload["error"]


def test_semantic_conflict_returns_nonzero_structured_json(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    cli = _module()
    engine, _ = _patch_database(monkeypatch, cli)
    spec_path = tmp_path / "experiment.json"
    spec_path.write_text(json.dumps(_spec_payload()), encoding="utf-8")

    def conflict(_connection, _spec):
        raise ValueError("experiment immutable semantic conflict")

    monkeypatch.setattr(cli.service, "register_experiment", conflict)

    assert cli.main(["register", "--spec", str(spec_path)]) != 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload == {
        "command": "register",
        "error": "experiment immutable semantic conflict",
        "error_type": "ValueError",
        "ok": False,
    }
    assert engine.disposed is True


def test_invalid_spec_returns_nonzero_json_without_opening_database(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    cli = _module()
    spec_path = tmp_path / "bad.json"
    spec_path.write_text("{}", encoding="utf-8")
    opened = False

    def forbidden_create_engine(*_args, **_kwargs):
        nonlocal opened
        opened = True
        raise AssertionError("invalid spec must fail before database access")

    monkeypatch.setattr(cli, "create_engine", forbidden_create_engine)

    assert cli.main(["register", "--spec", str(spec_path)]) != 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is False
    assert payload["command"] == "register"
    assert opened is False
