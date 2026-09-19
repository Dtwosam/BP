from __future__ import annotations

import json
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

try:
    from bp_engine.v3_research import cli as cli_module
except ImportError:
    cli_module = None

AS_OF = datetime(2026, 9, 19, 13, 45, tzinfo=UTC)
AS_OF_TEXT = "2026-09-19T13:45:00Z"


def test_v3_cli_exposes_only_readiness_and_plan_without_override_flags(
    tmp_path: Path,
) -> None:
    assert cli_module is not None
    parser = cli_module.build_parser()

    readiness = parser.parse_args(["readiness", "--as-of", AS_OF_TEXT])
    plan = parser.parse_args(
        ["plan", "--as-of", AS_OF_TEXT, "--output", str(tmp_path / "plan.json")]
    )
    prepare = parser.parse_args(
        [
            "prepare",
            "--plan",
            str(tmp_path / "plan.json"),
            "--output",
            str(tmp_path / "selection.json"),
            "--model-output",
            str(tmp_path / "model.joblib"),
        ]
    )

    assert readiness.command == "readiness"
    assert readiness.as_of == AS_OF
    assert plan.command == "plan"
    assert plan.as_of == AS_OF
    assert prepare.command == "prepare"

    help_text = parser.format_help().lower()
    for forbidden in (
        "prepare",
        "evaluate-holdout",
        "diagnosis-exclusions",
        "consumed-v2-final-holdout-exclusions",
        "train-hours",
        "validation-hours",
        "test-hours",
        "step-hours",
        "final-holdout-hours",
        "embargo-markets",
        "min-edge",
        "model",
        "grid",
    ):
        assert forbidden not in help_text

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "readiness",
                "--as-of",
                AS_OF_TEXT,
                "--diagnosis-exclusions",
                str(tmp_path / "diagnosis.json"),
            ]
        )
    with pytest.raises(SystemExit):
        parser.parse_args(["readiness", "--as-of", AS_OF_TEXT, "--train-hours", "24"])


def test_v3_cli_uses_postgresql_read_only_transaction() -> None:
    assert cli_module is not None
    statements: list[str] = []

    class Transaction:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class Connection:
        dialect = SimpleNamespace(name="postgresql")

        def begin(self):
            return Transaction()

        def exec_driver_sql(self, statement: str):
            statements.append(statement)

    connection = Connection()

    class ConnectionContext:
        def __enter__(self):
            return connection

        def __exit__(self, exc_type, exc, tb):
            return False

    class Engine:
        def connect(self):
            return ConnectionContext()

    result = cli_module._read_only(Engine(), lambda value: {"same": value is connection})

    assert result == {"same": True}
    assert statements == ["SET TRANSACTION READ ONLY"]


def test_v3_cli_readiness_is_artifact_free_and_prospective_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert cli_module is not None
    parser = cli_module.build_parser()
    args = parser.parse_args(["readiness", "--as-of", AS_OF_TEXT])
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        cli_module,
        "_settings",
        lambda args: SimpleNamespace(database_url="sqlite+pysqlite:///:memory:"),
    )
    monkeypatch.setattr(cli_module, "create_engine", lambda url: object())
    monkeypatch.setattr(
        cli_module,
        "_read_only",
        lambda engine, operation: operation(object()),
    )
    monkeypatch.setattr(
        cli_module,
        "_write_exclusive",
        lambda path, payload: pytest.fail("readiness must not write an artifact"),
    )

    def fake_readiness(connection, *, as_of):
        captured.update({"connection": connection, "as_of": as_of})
        return {
            "ready": False,
            "blocking_reasons": ("epoch_incomplete",),
            "coverage": {
                "market_count": 12,
                "coverage_input_sha256": "c" * 64,
            },
            "readiness_input_sha256": "r" * 64,
        }

    monkeypatch.setattr(cli_module, "assess_v3_gate_b_readiness", fake_readiness)
    payload = cli_module._run(args)

    assert captured["as_of"] == AS_OF
    assert payload == {
        "ready": False,
        "blocking_reasons": ["epoch_incomplete"],
        "market_count": 12,
        "coverage_input_sha256": "c" * 64,
        "readiness_input_sha256": "r" * 64,
    }


def test_v3_cli_plan_writes_full_plan_exclusively_and_returns_compact_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert cli_module is not None
    parser = cli_module.build_parser()
    output = tmp_path / "plan.json"
    args = parser.parse_args(
        ["plan", "--as-of", AS_OF_TEXT, "--output", str(output)]
    )
    full_plan = {
        "research_plan_version": "v3-gate-b-preregister-v2",
        "market_count": 864,
        "config_sha256": "a" * 64,
        "feature_manifest_sha256": "b" * 64,
        "plan_sha256": "p" * 64,
        "readiness_input_sha256": "r" * 64,
        "folds": [{"index": index} for index in range(5)],
        "final": {"holdout_condition_ids": ["condition-a", "condition-b"]},
    }
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        cli_module,
        "_settings",
        lambda args: SimpleNamespace(database_url="sqlite+pysqlite:///:memory:"),
    )
    monkeypatch.setattr(cli_module, "create_engine", lambda url: object())
    monkeypatch.setattr(
        cli_module,
        "_read_only",
        lambda engine, operation: operation(object()),
    )

    def fake_plan(connection, *, as_of):
        captured.update({"connection": connection, "as_of": as_of})
        return full_plan

    monkeypatch.setattr(cli_module, "build_v3_gate_b_plan", fake_plan)

    summary = cli_module._run(args)

    assert captured["as_of"] == AS_OF
    assert json.loads(output.read_text(encoding="utf-8")) == full_plan
    assert summary == {
        "research_plan_version": "v3-gate-b-preregister-v2",
        "market_count": 864,
        "ordinary_fold_count": 5,
        "final_holdout_market_count": 2,
        "config_sha256": "a" * 64,
        "feature_manifest_sha256": "b" * 64,
        "plan_sha256": "p" * 64,
        "readiness_input_sha256": "r" * 64,
        "readiness_blocking_reasons": [],
    }

    with pytest.raises(FileExistsError):
        cli_module._write_exclusive(str(output), full_plan)


def test_v3_cli_is_registered_as_a_console_script() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["scripts"]["bp-v3-gate-b"] == (
        "bp_engine.v3_research.cli:main"
    )
