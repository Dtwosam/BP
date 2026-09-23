from __future__ import annotations

from decimal import Decimal

from bp_engine.v3_live_gate.report import build_v3_live_gate_report


def _evaluation(value: str) -> dict[str, object]:
    v = Decimal(value)
    return {
        "raw_brier": v + Decimal("0.02"),
        "raw_log_loss": v + Decimal("0.20"),
        "calibrated_brier": v,
        "calibrated_log_loss": v + Decimal("0.15"),
    }


def test_v3_live_gate_report_is_evidence_only_and_outlier_aware() -> None:
    report = build_v3_live_gate_report(
        settlements=[
            {"realized_pnl": Decimal("2.00")},
            {"realized_pnl": Decimal("-1.00")},
            {"realized_pnl": Decimal("3.00")},
            {"realized_pnl": Decimal("8.00")},
        ],
        evaluations=[_evaluation("0.10"), _evaluation("0.12")],
        reconciliation={"status": "OK", "violation_count": 0},
        user_authorized=True,
        bootstrap_resamples=2_000,
    )
    assert report["explicit_user_live_authorization"] == "pass"
    assert report["sample"]["settled_trade_count"] == 4
    assert report["sample"]["wins"] == 3
    assert report["sample"]["losses"] == 1
    assert report["economics"]["realized_total_usd"] == "12.00"
    assert report["economics"]["largest_winner_usd"] == "8.00"
    assert report["economics"]["pnl_excluding_largest_winner_usd"] == "4.00"
    assert report["economics"]["max_losing_streak"] == 1
    assert report["diagnostics"]["profitable_without_largest_winner"] is True
    assert report["diagnostics"]["reconciliation_ok"] is True
    assert report["gate_boundary"]["master_live_gate_mutated"] is False
    assert report["gate_boundary"]["phase15_permitted"] is False
    assert report["live_trading_enabled"] is False
    assert report["real_money_mutation_performed"] is False


def test_v3_live_gate_report_does_not_hide_negative_robustness() -> None:
    report = build_v3_live_gate_report(
        settlements=[
            {"realized_pnl": Decimal("-5")},
            {"realized_pnl": Decimal("20")},
            {"realized_pnl": Decimal("-5")},
        ],
        evaluations=[],
        reconciliation={"status": "VIOLATION", "violation_count": 1},
        user_authorized=False,
        bootstrap_resamples=500,
    )
    assert report["explicit_user_live_authorization"] == "fail"
    assert report["economics"]["realized_total_usd"] == "10"
    assert report["economics"]["pnl_excluding_largest_winner_usd"] == "-10"
    assert report["diagnostics"]["profitable_without_largest_winner"] is False
    assert report["diagnostics"]["reconciliation_ok"] is False
