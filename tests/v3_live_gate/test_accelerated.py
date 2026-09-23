from __future__ import annotations

from bp_engine.v3_live_gate.accelerated import build_accelerated_v3_readiness
from bp_engine.v3_live_gate.calibration_audit import (
    CalibrationPoint,
    build_calibration_audit,
    expected_calibration_error,
)


def _v3_report() -> dict[str, object]:
    return {
        "sample": {
            "settled_trade_count": 76,
            "win_rate_95pct_ci": {"lower": 0.506, "upper": 0.719},
        },
        "economics": {
            "mean_95pct_ci_usd": {"lower": 1.64, "upper": 18.95},
            "pnl_excluding_largest_winner_usd": "450.80",
        },
        "calibration": {
            "evaluation_count": 519,
            "calibrated_brier_mean": 0.099,
            "calibrated_log_loss_mean": 0.317,
        },
        "diagnostics": {
            "bootstrap_mean_lower_bound_positive": True,
        },
        "reconciliation": {"status": "OK", "violation_count": 0},
    }


def test_ece_is_zero_for_perfect_two_bucket_calibration() -> None:
    points = [
        CalibrationPoint(probability=0.0, target=0),
        CalibrationPoint(probability=0.0, target=0),
        CalibrationPoint(probability=1.0, target=1),
        CalibrationPoint(probability=1.0, target=1),
    ]
    assert expected_calibration_error(points) == 0.0


def test_calibration_audit_returns_finite_regression() -> None:
    points = [
        CalibrationPoint(probability=0.1, target=0),
        CalibrationPoint(probability=0.2, target=0),
        CalibrationPoint(probability=0.4, target=0),
        CalibrationPoint(probability=0.6, target=1),
        CalibrationPoint(probability=0.8, target=1),
        CalibrationPoint(probability=0.9, target=1),
    ] * 10
    audit = build_calibration_audit(points, bootstrap_resamples=200)
    assert audit["evaluation_count"] == 60
    assert audit["valid_bootstrap_resamples"] >= 100


def test_accelerated_readiness_can_pass_without_strategy_mutation() -> None:
    audit = {
        "evaluation_count": 519,
        "ece_10_bin": 0.03,
        "intercept": 0.01,
        "slope": 1.01,
        "intercept_95pct_ci": {"lower": -0.1, "upper": 0.1},
        "slope_95pct_ci": {"lower": 0.9, "upper": 1.1},
    }
    report = build_accelerated_v3_readiness(
        v3_report=_v3_report(),
        calibration_audit=audit,
        frozen_selection={
            "ordinary_validation_economics_passed": True,
            "holdout_after_cost_pnl": 1.654224,
        },
    )
    assert report["phase15_candidate"] is True
    assert report["gates"]["walk_forward_results_stable_enough"]["status"] == "pass"
    assert report["gates"][
        "sufficiently_large_live_paper_sample_with_uncertainty"
    ]["status"] == "pass"
    assert report["gates"]["calibration_acceptable"]["status"] == "pass"
    assert report["strategy_mutation_performed"] is False
    assert report["real_money_activation_performed"] is False


def test_accelerated_readiness_fails_closed_on_calibration() -> None:
    audit = {
        "evaluation_count": 519,
        "ece_10_bin": 0.08,
        "intercept": 0.2,
        "slope": 0.7,
        "intercept_95pct_ci": {"lower": 0.1, "upper": 0.3},
        "slope_95pct_ci": {"lower": 0.6, "upper": 0.8},
    }
    report = build_accelerated_v3_readiness(
        v3_report=_v3_report(),
        calibration_audit=audit,
        frozen_selection={
            "ordinary_validation_economics_passed": True,
            "holdout_after_cost_pnl": 1.654224,
        },
    )
    assert report["phase15_candidate"] is False
    assert report["gates"]["calibration_acceptable"]["status"] == "fail"
