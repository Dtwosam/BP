from __future__ import annotations

import math
from dataclasses import dataclass

from bp_engine.improvement.statistics import BootstrapInterval


@dataclass(frozen=True)
class ComparisonResult:
    economic_delta: float | None
    economic_interval: BootstrapInterval | None
    calibration_log_loss_delta: float | None
    calibration_brier_delta: float | None
    promotion_eligible: bool
    ineligibility_reasons: tuple[str, ...]


def _finite_metric(value: float, *, name: str) -> float:
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{name} must be finite")
    return normalized


def compare_policies(
    *,
    economic_interval: BootstrapInterval | None,
    champion_log_loss: float,
    challenger_log_loss: float,
    champion_brier: float,
    challenger_brier: float,
    independent_confirmation_present: bool,
    integrity_ok: bool,
) -> ComparisonResult:
    """Apply the frozen Phase 13 promotion-eligibility gates without side effects."""

    champion_log_loss_value = _finite_metric(
        champion_log_loss,
        name="champion_log_loss",
    )
    challenger_log_loss_value = _finite_metric(
        challenger_log_loss,
        name="challenger_log_loss",
    )
    champion_brier_value = _finite_metric(champion_brier, name="champion_brier")
    challenger_brier_value = _finite_metric(challenger_brier, name="challenger_brier")

    reasons: set[str] = set()
    economic_delta: float | None = None
    if economic_interval is None:
        reasons.add("economic_uncertainty_not_positive")
    else:
        economic_delta = _finite_metric(
            economic_interval.mean_delta,
            name="economic_interval.mean_delta",
        )
        lower = _finite_metric(economic_interval.lower, name="economic_interval.lower")
        _finite_metric(economic_interval.upper, name="economic_interval.upper")
        if economic_delta <= 0 or lower <= 0:
            reasons.add("economic_uncertainty_not_positive")

    log_loss_delta = challenger_log_loss_value - champion_log_loss_value
    brier_delta = challenger_brier_value - champion_brier_value
    if log_loss_delta > 0:
        reasons.add("calibration_log_loss_worse")
    if brier_delta > 0:
        reasons.add("calibration_brier_worse")
    if not independent_confirmation_present:
        reasons.add("independent_confirmation_missing")
    if not integrity_ok:
        reasons.add("integrity_violation")

    ineligibility_reasons = tuple(sorted(reasons))
    return ComparisonResult(
        economic_delta=economic_delta,
        economic_interval=economic_interval,
        calibration_log_loss_delta=log_loss_delta,
        calibration_brier_delta=brier_delta,
        promotion_eligible=not ineligibility_reasons,
        ineligibility_reasons=ineligibility_reasons,
    )
