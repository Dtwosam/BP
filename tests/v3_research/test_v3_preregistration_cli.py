from __future__ import annotations

import json
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from bp_engine.v3_research.exclusions import build_exclusion_manifest

try:
    from bp_engine.v3_research import cli as cli_module
except ImportError:
    cli_module = None

AS_OF = datetime(2026, 9, 16, 13, 45, tzinfo=UTC)


def _write_manifest(path: Path, *, kind: str) -> Path:
    manifest = build_exclusion_manifest(kind=kind, condition_ids=())
    path.write_text(json.dumps(manifest.to_dict()), encoding="utf-8")
    return path


def _required_args(tmp_path: Path) -> list[str]:
    diagnosis = _write_manifest(tmp_path / "diagnosis.json", kind="diagnosis")
    consumed = _write_manifest(
        tmp_path / "consumed.json",
        kind="consumed_v2_final_holdout",
    )
    return [
        "--diagnosis-exclusions",
        str(diagnosis),
        "--consumed-v2-final-holdout-exclusions",
        str(consumed),
        "--as-of",
        "2026-09-16T13:45:00Z",
    ]


def test_v3_cli_exposes_only_readiness_and_plan_without_override_flags(
    tmp_path: Path,
) -> None:
    assert cli_module is not None
    parser = cli_module.build_parser()
    common = _required_args(tmp_path)

    readiness = parser.parse_args(["readiness", *common])
    plan = parser.parse_args(["plan", *common, "--output", str(tmp_path / "plan.json")])

    assert readiness.command == "readiness"
    assert readiness.as_of == AS_OF
    assert plan.command == "plan"
    assert plan.as_of == AS_OF

    help_text = parser.format_help().lower()
    for forbidden in (
        "prepare",
        "evaluate-holdout",
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
        parser.parse_args(["readiness", "--as-of", "2026-09-16T13:45:00Z"])
    with pytest.raises(SystemExit):
        parser.parse_args(["readiness", *common, "--train-hours", "24"])


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


def test_v3_cli_readiness_is_artifact_free_and_manifest_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert cli_module is not None
    parser = cli_module.build_parser()
    args = parser.parse_args(["readiness", *_required_args(tmp_path)])
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

    def fake_readiness(
        connection,
        *,
        as_of,
        diagnosis_exclusions,
        consumed_v2_final_holdout_exclusions,
    ):
        captured.update(
            {
                "as_of": as_of,
                "diagnosis": diagnosis_exclusions,
                "consumed": consumed_v2_final_holdout_exclusions,
            }
        )
        return {
            "ready": False,
            "blocking_reasons": ("epoch_incomplete",),
            "coverage": {
                "market_count": 12,
                "coverage_input_sha256": "c" * 64,
            },
            "diagnosis_exclusion_sha256": diagnosis_exclusions.sha256,
            "consumed_v2_final_holdout_exclusion_sha256": (
                consumed_v2_final_holdout_exclusions.sha256
            ),
            "readiness_input_sha256": "r" * 64,
        }

    monkeypatch.setattr(cli_module, "assess_v3_gate_b_readiness", fake_readiness)
    payload = cli_module._run(args)

    assert captured["as_of"] == AS_OF
    assert captured["diagnosis"].kind == "diagnosis"
    assert captured["consumed"].kind == "consumed_v2_final_holdout"
    assert payload == {
        "ready": False,
        "blocking_reasons": ["epoch_incomplete"],
        "market_count": 12,
        "coverage_input_sha256": "c" * 64,
        "diagnosis_exclusion_sha256": captured["diagnosis"].sha256,
        "consumed_v2_final_holdout_exclusion_sha256": captured["consumed"].sha256,
        "readiness_input_sha256": "r" * 64,
    }


def test_v3_cli_plan_writes_full_plan_exclusively_and_returns_compact_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert cli_module is not None
    parser = cli_module.build_parser()
    output = tmp_path / "plan.json"
    args = parser.parse_args(["plan", *_required_args(tmp_path), "--output", str(output)])
    full_plan = {
        "research_plan_version": "v3-gate-b-preregister-v1",
        "market_count": 864,
        "config_sha256": "a" * 64,
        "feature_manifest_sha256": "b" * 64,
        "plan_sha256": "p" * 64,
        "readiness_input_sha256": "r" * 64,
        "diagnosis_exclusion_sha256": "d" * 64,
        "consumed_v2_final_holdout_exclusion_sha256": "e" * 64,
        "folds": [{"index": index} for index in range(5)],
        "final": {"holdout_condition_ids": ["condition-a", "condition-b"]},
    }

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
        "build_v3_gate_b_plan",
        lambda connection, **kwargs: full_plan,
    )

    summary = cli_module._run(args)

    assert json.loads(output.read_text(encoding="utf-8")) == full_plan
    assert summary == {
        "research_plan_version": "v3-gate-b-preregister-v1",
        "market_count": 864,
        "ordinary_fold_count": 5,
        "final_holdout_market_count": 2,
        "config_sha256": "a" * 64,
        "feature_manifest_sha256": "b" * 64,
        "plan_sha256": "p" * 64,
        "readiness_input_sha256": "r" * 64,
        "diagnosis_exclusion_sha256": "d" * 64,
        "consumed_v2_final_holdout_exclusion_sha256": "e" * 64,
        "readiness_blocking_reasons": [],
    }

    with pytest.raises(FileExistsError):
        cli_module._write_exclusive(str(output), full_plan)


def test_v3_cli_is_registered_as_a_console_script() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["scripts"]["bp-v3-gate-b"] == (
        "bp_engine.v3_research.cli:main"
    )
