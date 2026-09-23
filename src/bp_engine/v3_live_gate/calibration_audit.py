from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite

import numpy as np

_EPS = 1e-9


@dataclass(frozen=True)
class CalibrationPoint:
    probability: float
    target: int

    def __post_init__(self) -> None:
        if not isfinite(self.probability) or not 0.0 <= self.probability <= 1.0:
            raise ValueError("probability must be finite and within [0, 1]")
        if self.target not in (0, 1):
            raise ValueError("target must be 0 or 1")


def expected_calibration_error(
    points: Sequence[CalibrationPoint],
    *,
    bins: int = 10,
) -> float:
    if not points:
        raise ValueError("calibration points must not be empty")
    if bins <= 1:
        raise ValueError("bins must be greater than one")
    probabilities = np.asarray([p.probability for p in points], dtype=float)
    targets = np.asarray([p.target for p in points], dtype=float)
    edges = np.linspace(0.0, 1.0, bins + 1)
    total = float(len(points))
    ece = 0.0
    for index in range(bins):
        if index == bins - 1:
            mask = (probabilities >= edges[index]) & (
                probabilities <= edges[index + 1]
            )
        else:
            mask = (probabilities >= edges[index]) & (
                probabilities < edges[index + 1]
            )
        count = int(mask.sum())
        if count == 0:
            continue
        confidence = float(probabilities[mask].mean())
        frequency = float(targets[mask].mean())
        ece += (count / total) * abs(confidence - frequency)
    return ece


def _fit_logistic_calibration(
    probabilities: np.ndarray,
    targets: np.ndarray,
) -> tuple[float, float]:
    clipped = np.clip(probabilities, _EPS, 1.0 - _EPS)
    logits = np.log(clipped / (1.0 - clipped))
    design = np.column_stack((np.ones(len(logits)), logits))
    beta = np.asarray([0.0, 1.0], dtype=float)
    ridge = np.eye(2, dtype=float) * 1e-8
    for _ in range(100):
        linear = np.clip(design @ beta, -35.0, 35.0)
        fitted = 1.0 / (1.0 + np.exp(-linear))
        weights = np.maximum(fitted * (1.0 - fitted), 1e-8)
        gradient = design.T @ (targets - fitted)
        hessian = design.T @ (design * weights[:, None]) + ridge
        step = np.linalg.solve(hessian, gradient)
        beta = beta + step
        if float(np.max(np.abs(step))) < 1e-10:
            break
    if not np.isfinite(beta).all():
        raise ValueError("calibration regression produced non-finite coefficients")
    return float(beta[0]), float(beta[1])


def calibration_regression(
    points: Sequence[CalibrationPoint],
    *,
    bootstrap_seed: int = 15,
    bootstrap_resamples: int = 2_000,
) -> dict[str, object]:
    if len(points) < 2:
        raise ValueError("at least two calibration points are required")
    if bootstrap_resamples <= 0:
        raise ValueError("bootstrap_resamples must be positive")
    probabilities = np.asarray([p.probability for p in points], dtype=float)
    targets = np.asarray([p.target for p in points], dtype=float)
    if len(np.unique(targets)) < 2:
        raise ValueError("both outcome classes are required")

    intercept, slope = _fit_logistic_calibration(probabilities, targets)
    generator = np.random.Generator(np.random.PCG64(bootstrap_seed))
    intercepts: list[float] = []
    slopes: list[float] = []
    for _ in range(bootstrap_resamples):
        indices = generator.integers(0, len(points), size=len(points))
        sampled_targets = targets[indices]
        if len(np.unique(sampled_targets)) < 2:
            continue
        sampled_probabilities = probabilities[indices]
        a, b = _fit_logistic_calibration(sampled_probabilities, sampled_targets)
        intercepts.append(a)
        slopes.append(b)
    if len(intercepts) < max(100, bootstrap_resamples // 2):
        raise ValueError("too few valid calibration bootstrap resamples")
    intercept_ci = np.percentile(np.asarray(intercepts), [2.5, 97.5])
    slope_ci = np.percentile(np.asarray(slopes), [2.5, 97.5])
    return {
        "intercept": intercept,
        "slope": slope,
        "intercept_95pct_ci": {
            "lower": float(intercept_ci[0]),
            "upper": float(intercept_ci[1]),
        },
        "slope_95pct_ci": {
            "lower": float(slope_ci[0]),
            "upper": float(slope_ci[1]),
        },
        "bootstrap_seed": bootstrap_seed,
        "bootstrap_resamples": bootstrap_resamples,
        "valid_bootstrap_resamples": len(intercepts),
    }


def build_calibration_audit(
    points: Sequence[CalibrationPoint],
    *,
    bootstrap_seed: int = 15,
    bootstrap_resamples: int = 2_000,
) -> dict[str, object]:
    regression = calibration_regression(
        points,
        bootstrap_seed=bootstrap_seed,
        bootstrap_resamples=bootstrap_resamples,
    )
    return {
        "evaluation_count": len(points),
        "ece_10_bin": expected_calibration_error(points, bins=10),
        **regression,
    }
