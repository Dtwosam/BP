from __future__ import annotations

import importlib
import importlib.util
import math
from decimal import Decimal

import pytest


def _evidence_module():
    spec = importlib.util.find_spec("bp_engine.execution.evidence")
    assert spec is not None, "prospective paper evidence module is missing"
    return importlib.import_module("bp_engine.execution.evidence")


def test_prospective_paper_evidence_reports_uncertainty_and_calibration() -> None:
    evidence = _evidence_module()

    report = evidence.summarize_prospective_paper_evidence(
        predictions=[
            {
                "prediction_id": "p1",
                "condition_id": "c1",
                "horizon_seconds": 300,
                "calibrated_probability": 0.8,
            },
            {
                "prediction_id": "p2",
                "condition_id": "c2",
                "horizon_seconds": 300,
                "calibrated_probability": 0.7,
            },
            {
                "prediction_id": "p3",
                "condition_id": "c3",
                "horizon_seconds": 900,
                "calibrated_probability": 0.3,
            },
            {
                "prediction_id": "p4",
                "condition_id": "c4",
                "horizon_seconds": 900,
                "calibrated_probability": 0.4,
            },
        ],
        evaluations=[
            {"prediction_id": "p1", "official_target": 1},
            {"prediction_id": "p2", "official_target": 1},
            {"prediction_id": "p3", "official_target": 0},
            {"prediction_id": "p4", "official_target": 0},
        ],
        settled_trades=[
            {"paper_order_id": "o1", "condition_id": "c1", "realized_pnl": Decimal("1.00")},
            {"paper_order_id": "o2", "condition_id": "c2", "realized_pnl": Decimal("-0.25")},
            {"paper_order_id": "o3", "condition_id": "c3", "realized_pnl": Decimal("0.50")},
            {"paper_order_id": "o4", "condition_id": "c4", "realized_pnl": Decimal("-0.10")},
        ],
        reconciliation={"status": "OK", "violation_count": 0},
        seed=7,
        resamples=1_000,
    )

    assert report.evaluated_prediction_count == 4
    assert report.settled_trade_count == 4
    assert report.total_realized_pnl == Decimal("1.15")
    assert report.mean_realized_pnl == pytest.approx(0.2875)
    assert report.pnl_mean_ci_lower <= report.mean_realized_pnl <= report.pnl_mean_ci_upper
    assert report.pnl_bootstrap_resamples == 1_000
    assert report.accuracy == pytest.approx(1.0)
    assert report.brier_score == pytest.approx(0.095)
    expected_log_loss = -sum(
        [math.log(0.8), math.log(0.7), math.log(0.7), math.log(0.6)]
    ) / 4
    assert report.log_loss == pytest.approx(expected_log_loss)
    assert report.mean_calibrated_probability == pytest.approx(0.55)
    assert report.observed_up_rate == pytest.approx(0.5)
    assert report.aggregate_calibration_gap == pytest.approx(0.05)
    assert report.reconciliation_status == "OK"
    assert report.reconciliation_violation_count == 0


def test_prospective_paper_evidence_keeps_empty_metrics_explicit() -> None:
    evidence = _evidence_module()

    report = evidence.summarize_prospective_paper_evidence(
        predictions=[],
        evaluations=[],
        settled_trades=[],
        reconciliation={"status": "OK", "violation_count": 0},
        seed=7,
        resamples=1_000,
    )

    assert report.evaluated_prediction_count == 0
    assert report.settled_trade_count == 0
    assert report.total_realized_pnl == Decimal("0")
    assert report.mean_realized_pnl is None
    assert report.pnl_mean_ci_lower is None
    assert report.pnl_mean_ci_upper is None
    assert report.accuracy is None
    assert report.brier_score is None
    assert report.log_loss is None
    assert report.aggregate_calibration_gap is None
