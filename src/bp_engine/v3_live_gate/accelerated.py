from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

FROZEN_HOLDOUT_LOG_LOSS = 0.35419212970900277
FROZEN_HOLDOUT_BRIER = 0.10943703117284813


def _status(passed: bool, reason: str) -> dict[str, object]:
    return {"status": "pass" if passed else "fail", "reason": reason}


def build_accelerated_v3_readiness(
    *,
    v3_report: Mapping[str, object],
    calibration_audit: Mapping[str, object],
    frozen_selection: Mapping[str, object],
) -> dict[str, object]:
    """Map already-frozen V3 evidence to the Master live-gate rows.

    The economic/sample rule is inherited from the accepted Phase 13 principle:
    there is no magic count; independent prospective evidence is sufficient only
    when the 95% lower confidence bound for mean after-cost expectancy is above
    zero. The calibration intercept/slope acceptance rule is separately frozen
    before those prospective diagnostics are read.
    """

    sample = v3_report["sample"]
    economics = v3_report["economics"]
    calibration = v3_report["calibration"]
    diagnostics = v3_report["diagnostics"]
    reconciliation = v3_report["reconciliation"]

    pnl_ci = economics["mean_95pct_ci_usd"]
    sample_pass = (
        int(sample["settled_trade_count"]) > 0
        and int(calibration["evaluation_count"]) > 0
        and float(pnl_ci["lower"]) > 0.0
    )

    ordinary_gate = bool(frozen_selection["ordinary_validation_economics_passed"])
    holdout_positive = float(frozen_selection["holdout_after_cost_pnl"]) > 0.0
    walk_forward_pass = (
        ordinary_gate
        and holdout_positive
        and bool(diagnostics["bootstrap_mean_lower_bound_positive"])
    )

    intercept_ci = calibration_audit["intercept_95pct_ci"]
    slope_ci = calibration_audit["slope_95pct_ci"]
    calibration_pass = (
        int(calibration_audit["evaluation_count"]) > 0
        and float(calibration["calibrated_brier_mean"]) <= FROZEN_HOLDOUT_BRIER
        and float(calibration["calibrated_log_loss_mean"]) <= FROZEN_HOLDOUT_LOG_LOSS
        and float(intercept_ci["lower"]) <= 0.0 <= float(intercept_ci["upper"])
        and float(slope_ci["lower"]) <= 1.0 <= float(slope_ci["upper"])
    )

    reconciliation_pass = (
        str(reconciliation["status"]).upper() == "OK"
        and int(reconciliation["violation_count"]) == 0
    )

    return {
        "schema_version": 1,
        "scope": "frozen_v3_same_day_live_readiness_v1",
        "strategy_mutation_performed": False,
        "threshold_tuning_performed": False,
        "automatic_promotion": False,
        "live_trading_enabled": False,
        "sample_rule": {
            "reference": "accepted Phase 13 uncertainty principle",
            "fixed_minimum_trade_count": None,
            "require_mean_pnl_95pct_lower_above_zero": True,
        },
        "calibration_rule": {
            "frozen_before_reliability_audit": True,
            "reference": "frozen pre-paper V3 holdout plus prospective reliability regression",
            "max_brier": FROZEN_HOLDOUT_BRIER,
            "max_log_loss": FROZEN_HOLDOUT_LOG_LOSS,
            "ece_10_bin": "descriptive_only",
            "require_intercept_95pct_ci_contains_zero": True,
            "require_slope_95pct_ci_contains_one": True,
        },
        "walk_forward_rule": {
            "ordinary_validation_gate_source": "v3-gate-b-preregister-v2",
            "ordinary_validation_economics_passed": ordinary_gate,
            "untouched_holdout_after_cost_pnl": float(
                frozen_selection["holdout_after_cost_pnl"]
            ),
            "require_positive_prospective_mean_pnl_lower_bound": True,
        },
        "gates": {
            "walk_forward_results_stable_enough": _status(
                walk_forward_pass,
                "Frozen five-fold validation economics, untouched holdout economics, and prospective paper uncertainty must all be positive.",
            ),
            "sufficiently_large_live_paper_sample_with_uncertainty": _status(
                sample_pass,
                "No magic count is introduced; the prospective mean after-cost P&L 95% lower bound must be strictly positive.",
            ),
            "positive_after_cost_profitability": _status(
                bool(diagnostics["bootstrap_mean_lower_bound_positive"]),
                "Prospective realized after-cost mean-P&L bootstrap lower bound must be positive.",
            ),
            "calibration_acceptable": _status(
                calibration_pass,
                "Prospective Brier/log loss must not degrade versus the frozen pre-paper holdout and the predeclared calibration intercept/slope confidence intervals must contain 0/1 respectively.",
            ),
            "order_execution_and_reconciliation_tested": _status(
                reconciliation_pass,
                "Frozen V3 order/fill/settlement reconciliation must be OK with zero violations.",
            ),
        },
        "calibration_audit": dict(calibration_audit),
        "v3_report": dict(v3_report),
        "frozen_selection": dict(frozen_selection),
        "phase15_candidate": (
            walk_forward_pass
            and sample_pass
            and bool(diagnostics["bootstrap_mean_lower_bound_positive"])
            and calibration_pass
            and reconciliation_pass
        ),
        "geography_required_separately": True,
        "real_money_activation_performed": False,
    }
