from __future__ import annotations

from decimal import Decimal

from bp_engine.prospective_evidence.service import build_prospective_evidence_report


def _evaluation(
    prediction_id: str,
    *,
    raw_brier: float,
    raw_log_loss: float,
    calibrated_brier: float,
    calibrated_log_loss: float,
) -> dict[str, object]:
    return {
        "prediction_id": prediction_id,
        "raw_brier": raw_brier,
        "raw_log_loss": raw_log_loss,
        "calibrated_brier": calibrated_brier,
        "calibrated_log_loss": calibrated_log_loss,
    }


def test_positive_after_cost_evidence_can_pass_without_promoting_live_gate() -> None:
    report = build_prospective_evidence_report(
        settlements=[
            {"paper_order_id": "o1", "realized_pnl": Decimal("1.00")},
            {"paper_order_id": "o2", "realized_pnl": Decimal("1.20")},
            {"paper_order_id": "o3", "realized_pnl": Decimal("0.80")},
            {"paper_order_id": "o4", "realized_pnl": Decimal("1.10")},
        ],
        evaluations=[
            _evaluation(
                "p1",
                raw_brier=0.12,
                raw_log_loss=0.40,
                calibrated_brier=0.10,
                calibrated_log_loss=0.35,
            ),
            _evaluation(
                "p2",
                raw_brier=0.18,
                raw_log_loss=0.55,
                calibrated_brier=0.14,
                calibrated_log_loss=0.45,
            ),
        ],
        reconciliation={"status": "OK", "violation_count": 0},
        master_live_gate={
            "overall_live_gate": "fail",
            "master_live_gate": {"geographic_compliance_eligible": "fail"},
        },
        bootstrap_seed=14,
        bootstrap_resamples=2_000,
    )

    assert report["automatic_promotion"] is False
    assert report["sample"] == {"settled_trade_count": 4, "evaluation_count": 2}
    assert report["after_cost_pnl"]["realized_total_usd"] == "4.10"
    assert report["after_cost_pnl"]["realized_mean_usd"] == "1.025"
    assert report["after_cost_pnl"]["mean_95pct_ci_usd"]["lower"] > 0
    assert report["after_cost_pnl"]["mean_95pct_ci_usd"]["upper"] > 0
    assert report["calibration"]["calibrated_brier_mean"] == 0.12
    assert report["calibration"]["calibrated_log_loss_mean"] == 0.4
    assert report["evidence_gates"]["positive_after_cost_profitability"]["status"] == "pass"
    assert (
        report["evidence_gates"]["sufficiently_large_live_paper_sample_with_uncertainty"][
            "status"
        ]
        == "insufficient_evidence"
    )
    assert report["evidence_gates"]["calibration_acceptable"]["status"] == "insufficient_evidence"
    assert report["evidence_gates"]["order_execution_and_reconciliation_tested"]["status"] == "pass"
    assert report["master_live_gate"]["overall_live_gate"] == "fail"


def test_non_positive_realized_pnl_fails_profitability_and_reconciliation_violations_fail() -> None:
    report = build_prospective_evidence_report(
        settlements=[
            {"paper_order_id": "o1", "realized_pnl": Decimal("0.25")},
            {"paper_order_id": "o2", "realized_pnl": Decimal("-0.50")},
        ],
        evaluations=[],
        reconciliation={"status": "VIOLATION", "violation_count": 2},
        master_live_gate={"overall_live_gate": "fail", "master_live_gate": {}},
        bootstrap_seed=14,
        bootstrap_resamples=500,
    )

    assert report["after_cost_pnl"]["realized_total_usd"] == "-0.25"
    assert report["evidence_gates"]["positive_after_cost_profitability"]["status"] == "fail"
    assert report["evidence_gates"]["order_execution_and_reconciliation_tested"]["status"] == "fail"
    assert report["evidence_gates"]["calibration_acceptable"]["status"] == "insufficient_evidence"


def test_empty_evidence_never_becomes_an_optimistic_pass() -> None:
    report = build_prospective_evidence_report(
        settlements=[],
        evaluations=[],
        reconciliation={"status": "OK", "violation_count": 0},
        master_live_gate={"overall_live_gate": "fail", "master_live_gate": {}},
        bootstrap_seed=14,
        bootstrap_resamples=500,
    )

    assert report["after_cost_pnl"]["mean_95pct_ci_usd"] is None
    assert (
        report["evidence_gates"]["positive_after_cost_profitability"]["status"]
        == "insufficient_evidence"
    )
    assert (
        report["evidence_gates"]["sufficiently_large_live_paper_sample_with_uncertainty"][
            "status"
        ]
        == "insufficient_evidence"
    )
    assert report["evidence_gates"]["calibration_acceptable"]["status"] == "insufficient_evidence"
    assert report["automatic_promotion"] is False
