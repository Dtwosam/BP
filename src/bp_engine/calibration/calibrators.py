from __future__ import annotations

import math

import numpy as np
from sklearn.linear_model import LogisticRegression

from bp_engine.calibration.models import (
    CalibrationCandidate,
    CalibrationFit,
    CalibrationSelection,
)
from bp_engine.modeling.metrics import evaluate_probabilities
from bp_engine.modeling.models import SupervisedRow
from bp_engine.modeling.split import equal_market_weights

_EPSILON = 1e-6
_RANDOM_STATE = 20260825


class CalibrationRejected(ValueError):
    """Raised when a calibration challenger violates the V1 contract."""


def clip_probability(value: float) -> float:
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError("probability must be finite")
    return min(max(numeric, _EPSILON), 1.0 - _EPSILON)


def _validate_inputs(
    rows: tuple[SupervisedRow, ...], probabilities: tuple[float, ...]
) -> tuple[float, ...]:
    if not rows:
        raise ValueError("rows must not be empty")
    if len(rows) != len(probabilities):
        raise ValueError("rows and probabilities must have equal length")
    if any(row.target not in (0, 1) for row in rows):
        raise ValueError("targets must be binary")
    return tuple(clip_probability(probability) for probability in probabilities)


def _logit(value: float) -> float:
    probability = clip_probability(value)
    return math.log(probability / (1.0 - probability))


class IdentityCalibrator:
    def fit(
        self,
        rows: tuple[SupervisedRow, ...],
        probabilities: tuple[float, ...],
        weights: tuple[float, ...] | None = None,
    ) -> CalibrationFit:
        _validate_inputs(rows, probabilities)
        if weights is not None and len(weights) != len(rows):
            raise ValueError("weights must match rows")
        return CalibrationFit(method="identity", intercept=None, coefficient=None)

    def predict(self, probabilities: tuple[float, ...]) -> tuple[float, ...]:
        return tuple(clip_probability(probability) for probability in probabilities)

    @classmethod
    def from_fit(cls, fit: CalibrationFit) -> IdentityCalibrator:
        if fit.method != "identity":
            raise ValueError("identity calibrator requires identity fit")
        return cls()


class PlattCalibrator:
    def __init__(self, fit: CalibrationFit | None = None) -> None:
        self._fit = fit

    def fit(
        self,
        rows: tuple[SupervisedRow, ...],
        probabilities: tuple[float, ...],
        weights: tuple[float, ...],
    ) -> CalibrationFit:
        clipped = _validate_inputs(rows, probabilities)
        if len(weights) != len(rows):
            raise ValueError("weights must match rows")
        if any(not math.isfinite(weight) or weight <= 0 for weight in weights):
            raise ValueError("weights must be positive and finite")
        if {row.target for row in rows} != {0, 1}:
            raise CalibrationRejected("Platt calibration requires both target classes")

        features = np.asarray([[_logit(probability)] for probability in clipped], dtype=float)
        targets = np.asarray([row.target for row in rows], dtype=int)
        sample_weight = np.asarray(weights, dtype=float)
        model = LogisticRegression(
            solver="lbfgs",
            max_iter=1000,
            random_state=_RANDOM_STATE,
        )
        model.fit(features, targets, sample_weight=sample_weight)
        intercept = float(model.intercept_[0])
        coefficient = float(model.coef_[0][0])
        if not math.isfinite(intercept) or not math.isfinite(coefficient):
            raise CalibrationRejected("Platt parameters must be finite")
        if coefficient <= 0:
            raise CalibrationRejected("Platt coefficient must be positive")
        fit = CalibrationFit(
            method="platt",
            intercept=intercept,
            coefficient=coefficient,
        )
        self._fit = fit
        return fit

    def predict(self, probabilities: tuple[float, ...]) -> tuple[float, ...]:
        if self._fit is None:
            raise RuntimeError("Platt calibrator must be fitted before prediction")
        assert self._fit.intercept is not None
        assert self._fit.coefficient is not None
        result: list[float] = []
        for probability in probabilities:
            score = self._fit.intercept + self._fit.coefficient * _logit(probability)
            if score >= 0:
                calibrated = 1.0 / (1.0 + math.exp(-score))
            else:
                exp_score = math.exp(score)
                calibrated = exp_score / (1.0 + exp_score)
            result.append(clip_probability(calibrated))
        return tuple(result)

    @classmethod
    def from_fit(cls, fit: CalibrationFit) -> PlattCalibrator:
        if fit.method != "platt" or fit.intercept is None or fit.coefficient is None:
            raise ValueError("Platt calibrator requires a complete platt fit")
        if fit.coefficient <= 0:
            raise CalibrationRejected("Platt coefficient must be positive")
        return cls(fit=fit)


def _predict_from_fit(
    fit: CalibrationFit, probabilities: tuple[float, ...]
) -> tuple[float, ...]:
    if fit.method == "identity":
        return IdentityCalibrator.from_fit(fit).predict(probabilities)
    if fit.method == "platt":
        return PlattCalibrator.from_fit(fit).predict(probabilities)
    raise ValueError(f"unsupported calibration method: {fit.method}")


def select_calibrator(
    train_rows: tuple[SupervisedRow, ...],
    train_probabilities: tuple[float, ...],
    validation_rows: tuple[SupervisedRow, ...],
    validation_probabilities: tuple[float, ...],
) -> CalibrationSelection:
    _validate_inputs(train_rows, train_probabilities)
    _validate_inputs(validation_rows, validation_probabilities)

    identity = IdentityCalibrator()
    identity_fit = identity.fit(
        train_rows,
        train_probabilities,
        equal_market_weights(train_rows),
    )
    identity_validation = identity.predict(validation_probabilities)
    identity_metrics = evaluate_probabilities(
        validation_rows,
        identity_validation,
        equal_market_weights(validation_rows),
    )
    candidates = [
        CalibrationCandidate(
            method="identity",
            fit=identity_fit,
            validation_metrics=identity_metrics,
        )
    ]

    selected_fit = identity_fit
    selected_metrics = identity_metrics
    try:
        platt = PlattCalibrator()
        platt_fit = platt.fit(
            train_rows,
            train_probabilities,
            equal_market_weights(train_rows),
        )
        platt_validation = platt.predict(validation_probabilities)
        platt_metrics = evaluate_probabilities(
            validation_rows,
            platt_validation,
            equal_market_weights(validation_rows),
        )
        candidates.append(
            CalibrationCandidate(
                method="platt",
                fit=platt_fit,
                validation_metrics=platt_metrics,
            )
        )
        if (
            platt_metrics.log_loss < identity_metrics.log_loss
            and platt_metrics.brier_score < identity_metrics.brier_score
        ):
            selected_fit = platt_fit
            selected_metrics = platt_metrics
    except CalibrationRejected:
        pass

    return CalibrationSelection(
        method=selected_fit.method,
        fit=selected_fit,
        validation_metrics=selected_metrics,
        candidates=tuple(candidates),
    )


def apply_calibration(
    fit: CalibrationFit, probabilities: tuple[float, ...]
) -> tuple[float, ...]:
    return _predict_from_fit(fit, probabilities)
