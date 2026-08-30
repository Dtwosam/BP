from __future__ import annotations

import importlib
import importlib.util
import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from bp_engine.config import Settings, TradingMode
from bp_engine.execution.evidence import (
    ProspectivePaperEvidenceInputs,
    ProspectivePaperEvidenceReport,
)


def _cli_module():
    spec = importlib.util.find_spec("bp_engine.execution.evidence_cli")
    assert spec is not None, "prospective paper evidence CLI module is missing"
    return importlib.import_module("bp_engine.execution.evidence_cli")


def test_parser_requires_timezone_aware_since_and_exposes_no_live_controls() -> None:
    cli = _cli_module()
    parser = cli.build_parser()

    parsed = parser.parse_args(["--since", "2026-08-30T20:47:45Z"])
    assert parsed.since == datetime(2026, 8, 30, 20, 47, 45, tzinfo=UTC)

    with pytest.raises(SystemExit):
        parser.parse_args(["--since", "2026-08-30T20:47:45"])

    option_strings = {
        option
        for action in parser._actions
        for option in action.option_strings
    }
    forbidden = {
        "--database-url",
        "--private-key",
        "--wallet",
        "--live-trading-enabled",
        "--order",
    }
    assert option_strings.isdisjoint(forbidden)


def test_main_emits_one_read_only_json_report(monkeypatch, capsys) -> None:
    cli = _cli_module()
    since = datetime(2026, 8, 30, 20, 47, 45, tzinfo=UTC)
    settings = Settings(
        mode=TradingMode.RESEARCH,
        live_trading_enabled=False,
        max_trade_size_usd=0,
        max_daily_loss_usd=0,
        database_url="postgresql+psycopg://example.invalid/bp",
    )
    inputs = ProspectivePaperEvidenceInputs(
        predictions=(),
        evaluations=(),
        settled_trades=(),
    )
    report = ProspectivePaperEvidenceReport(
        evaluated_prediction_count=0,
        settled_trade_count=0,
        total_realized_pnl=Decimal("0"),
        mean_realized_pnl=None,
        pnl_mean_ci_lower=None,
        pnl_mean_ci_upper=None,
        pnl_bootstrap_resamples=10_000,
        accuracy=None,
        brier_score=None,
        log_loss=None,
        mean_calibrated_probability=None,
        observed_up_rate=None,
        aggregate_calibration_gap=None,
        reconciliation_status="OK",
        reconciliation_violation_count=0,
    )

    class FakeEngine:
        def dispose(self) -> None:
            pass

    fake_engine = FakeEngine()
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "create_engine", lambda *_args, **_kwargs: fake_engine)
    monkeypatch.setattr(
        cli,
        "PostgresProspectivePaperEvidenceReader",
        lambda _engine: SimpleNamespace(load=lambda **_kwargs: inputs),
    )
    monkeypatch.setattr(
        cli,
        "PostgresDashboardRepository",
        lambda _engine: SimpleNamespace(
            get_paper_execution_evidence=lambda **_kwargs: {
                "paper_pnl": {
                    "reconciliation": {"status": "OK", "violation_count": 0}
                }
            }
        ),
    )
    monkeypatch.setattr(
        cli,
        "summarize_prospective_paper_evidence",
        lambda **_kwargs: report,
    )

    assert cli.main(["--since", since.isoformat()]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["since"] == "2026-08-30T20:47:45+00:00"
    assert payload["total_realized_pnl"] == "0"
    assert payload["reconciliation_status"] == "OK"
    assert payload["interpretation"] == "evidence_only_no_automatic_live_gate_pass"


def test_main_rejects_any_money_enabled_runtime(monkeypatch) -> None:
    cli = _cli_module()
    unsafe = replace(Settings(), live_trading_enabled=True)
    monkeypatch.setattr(cli, "get_settings", lambda: unsafe)

    with pytest.raises(RuntimeError, match="LIVE_TRADING_ENABLED=false"):
        cli.main(["--since", "2026-08-30T20:47:45Z"])


def test_thin_script_entrypoint_exists() -> None:
    script = Path("scripts/prospective_paper_evidence.py")
    assert script.exists()
    source = script.read_text(encoding="utf-8")
    assert "bp_engine.execution.evidence_cli" in source
    assert "main" in source
