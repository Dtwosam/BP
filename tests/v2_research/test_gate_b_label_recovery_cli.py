from __future__ import annotations

import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run_v2_gate_b_label_recovery.py"


def test_parser_exposes_audit_and_recover_for_existing_frozen_plan_only() -> None:
    cli = importlib.import_module("bp_engine.v2_research.label_recovery_cli")
    parser = cli.build_parser()

    audit = parser.parse_args(["audit", "--plan", "/tmp/plan.json"])
    recover = parser.parse_args(["recover", "--plan", "/tmp/plan.json"])

    assert audit.command == "audit"
    assert audit.plan == "/tmp/plan.json"
    assert recover.command == "recover"
    assert recover.plan == "/tmp/plan.json"

    choices = parser._subparsers._group_actions[0].choices
    assert set(choices) == {"audit", "recover"}
    assert "frozen" in parser.description.lower()


def test_safety_is_checked_before_engine_or_network_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    cli = importlib.import_module("bp_engine.v2_research.label_recovery_cli")
    events: list[str] = []

    class UnsafeSettings:
        database_url = "sqlite://"

    def fake_settings(_args):
        events.append("settings")
        return UnsafeSettings()

    def fail_safety(_settings):
        events.append("safety")
        raise ValueError("unsafe")

    def forbidden_engine(*_args, **_kwargs):
        events.append("engine")
        raise AssertionError("engine constructed before safety")

    monkeypatch.setattr(cli, "_settings", fake_settings)
    monkeypatch.setattr(cli, "ensure_label_recovery_safety", fail_safety)
    monkeypatch.setattr(cli, "create_engine", forbidden_engine)

    with pytest.raises(ValueError, match="unsafe"):
        cli._run(cli.build_parser().parse_args(["audit", "--plan", "/tmp/plan.json"]))

    assert events == ["settings", "safety"]


def test_audit_is_database_read_only_and_recover_is_the_only_network_path() -> None:
    cli_source = (
        ROOT / "src" / "bp_engine" / "v2_research" / "label_recovery_cli.py"
    ).read_text(encoding="utf-8")

    assert 'if args.command == "audit"' in cli_source
    assert "SET TRANSACTION READ ONLY" in cli_source
    assert 'if args.command == "recover"' in cli_source
    assert "GammaClient()" in cli_source
    assert "recover_gate_b_non_holdout_labels" in cli_source
    assert "build_gate_b_plan" not in cli_source
    assert "evaluate_gate_b_holdout" not in cli_source


def test_thin_label_recovery_entrypoint_exists() -> None:
    assert SCRIPT.exists()
    source = SCRIPT.read_text(encoding="utf-8")
    assert "bp_engine.v2_research.label_recovery_cli import main" in source
    assert "main()" in source
