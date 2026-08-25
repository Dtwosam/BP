from __future__ import annotations

from dataclasses import dataclass

from bp_engine.modeling.models import MetricSummary

CALIBRATION_VERSION = "platt-or-identity-v1"


@dataclass(frozen=True)
class CalibrationFit:
    method: str
    intercept: float | None
    coefficient: float | None


@dataclass(frozen=True)
class CalibrationCandidate:
    method: str
    fit: CalibrationFit
    validation_metrics: MetricSummary


@dataclass(frozen=True)
class CalibrationSelection:
    method: str
    fit: CalibrationFit
    validation_metrics: MetricSummary
    candidates: tuple[CalibrationCandidate, ...]
