from __future__ import annotations

import json
from decimal import Decimal

import pytest

from bp_engine.config import Settings
from bp_engine.prospective_evidence.cli import (
    build_parser,
    ensure_money_disabled,
    load_master_live_gate,
)


def test_cli_exposes_report_only_and_loads_master_gate(tmp_path) -> None:
    state_path = tmp_path / "PROJECT_STATE.json"
    state_path.write_text(
        json.dumps(
            {
                "phase_14_checkpoint": {
                    "overall_live_gate": "fail",
                    "master_live_gate": {
                        "positive_after_cost_profitability": "fail",
                        "calibration_acceptable": "insufficient_evidence",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    parser = build_parser()
    args = parser.parse_args(
        [
            "report",
            "--project-state",
            str(state_path),
            "--bootstrap-resamples",
            "500",
        ]
    )

    assert args.command == "report"
    assert args.bootstrap_resamples == 500
    assert load_master_live_gate(state_path) == {
        "overall_live_gate": "fail",
        "master_live_gate": {
            "positive_after_cost_profitability": "fail",
            "calibration_acceptable": "insufficient_evidence",
        },
    }
    with pytest.raises(SystemExit):
        parser.parse_args(["promote"])


def test_money_disabled_guard_rejects_any_real_money_interlock() -> None:
    safe = Settings().model_copy(
        update={
            "live_trading_enabled": False,
            "max_trade_size_usd": Decimal("0"),
            "max_daily_loss_usd": Decimal("0"),
        }
    )
    ensure_money_disabled(safe)

    for unsafe in (
        safe.model_copy(update={"live_trading_enabled": True}),
        safe.model_copy(update={"max_trade_size_usd": Decimal("1")}),
        safe.model_copy(update={"max_daily_loss_usd": Decimal("1")}),
    ):
        with pytest.raises(RuntimeError, match="money-disabled"):
            ensure_money_disabled(unsafe)
