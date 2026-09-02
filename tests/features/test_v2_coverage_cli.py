import json
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine, event

from bp_engine.features import v2_coverage_cli
from bp_engine.storage import schema


class _Context:
    def __init__(self, value, events: list[str], enter_name: str, exit_name: str) -> None:
        self.value = value
        self.events = events
        self.enter_name = enter_name
        self.exit_name = exit_name

    def __enter__(self):
        self.events.append(self.enter_name)
        return self.value

    def __exit__(self, exc_type, exc, tb) -> None:
        self.events.append(self.exit_name)


class _FakeConnection:
    def __init__(self, dialect_name: str, events: list[str]) -> None:
        self.dialect = SimpleNamespace(name=dialect_name)
        self.events = events

    def begin(self):
        return _Context(self, self.events, "transaction_enter", "transaction_exit")

    def exec_driver_sql(self, statement: str) -> None:
        self.events.append(statement)


class _FakeEngine:
    def __init__(self, dialect_name: str, events: list[str]) -> None:
        self.connection = _FakeConnection(dialect_name, events)
        self.events = events

    def connect(self):
        return _Context(self.connection, self.events, "connect_enter", "connect_exit")


def test_postgres_report_sets_transaction_read_only_before_query(monkeypatch) -> None:
    events: list[str] = []
    engine = _FakeEngine("postgresql", events)

    def _report(_connection):
        events.append("report")
        return {"row_count": 0, "policy_selected": False, "automatic_promotion": False}

    monkeypatch.setattr(v2_coverage_cli, "build_v2_coverage_report", _report)
    report = v2_coverage_cli.run_read_only_report(engine)

    assert report["policy_selected"] is False
    assert events[:4] == [
        "connect_enter",
        "transaction_enter",
        "SET TRANSACTION READ ONLY",
        "report",
    ]
    assert events[-2:] == ["transaction_exit", "connect_exit"]


def test_sqlite_report_path_issues_selects_only() -> None:
    engine = create_engine("sqlite://")
    schema.metadata.create_all(engine)
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _capture(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        statements.append(statement.strip().lower())

    report = v2_coverage_cli.run_read_only_report(engine)

    assert report["row_count"] == 0
    assert statements
    assert all(statement.startswith("select") for statement in statements)


def test_main_prints_deterministic_json_with_no_policy_selection(monkeypatch, capsys) -> None:
    report = {
        "automatic_promotion": False,
        "coverage_input_sha256": "a" * 64,
        "policy_selected": False,
        "row_count": 0,
    }
    monkeypatch.setattr(v2_coverage_cli, "_run", lambda _args: report)

    assert v2_coverage_cli.main(["--database-url", "sqlite://"]) == 0
    rendered = capsys.readouterr().out
    assert json.loads(rendered) == report
    assert rendered == json.dumps(report, indent=2, sort_keys=True) + "\n"


def test_coverage_cli_and_script_are_read_only_and_offline() -> None:
    source = Path("src/bp_engine/features/v2_coverage_cli.py").read_text().lower()
    script = Path("scripts/report_v2_feature_coverage.py").read_text().lower()

    assert "set transaction read only" in source
    assert "build_v2_coverage_report" in source
    assert "v2_coverage_cli import main" in script
    for forbidden in (
        "httpx",
        "websocket",
        "insert(",
        "update(",
        "delete(",
        "market_labels",
        "live_prediction",
        "paper_settlement",
    ):
        assert forbidden not in source
        assert forbidden not in script
