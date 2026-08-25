from __future__ import annotations

import math
from collections import defaultdict

from bp_engine.modeling.models import MetricSummary, SupervisedRow

_CALIBRATION_BOUNDS = (
    (0.50, 0.55, "50-55"),
    (0.55, 0.60, "55-60"),
    (0.60, 0.65, "60-65"),
    (0.65, 0.70, "65-70"),
    (0.70, 0.75, "70-75"),
    (0.75, 0.80, "75-80"),
    (0.80, 0.85, "80-85"),
    (0.85, 0.90, "85-90"),
    (0.90, 1.000000000001, "90+"),
)
_COVERAGE_THRESHOLDS = (0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90)


def _validate(
    rows: tuple[SupervisedRow, ...],
    probabilities: tuple[float, ...],
    weights: tuple[float, ...],
) -> None:
    if not rows:
        raise ValueError("rows must not be empty")
    if len(rows) != len(probabilities) or len(rows) != len(weights):
        raise ValueError("rows, probabilities, and weights must have equal length")
    if any(not math.isfinite(weight) or weight <= 0 for weight in weights):
        raise ValueError("weights must be positive and finite")
    for probability in probabilities:
        if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
            raise ValueError("probability must be finite and within [0, 1]")


def evaluate_probabilities(
    rows: tuple[SupervisedRow, ...],
    probabilities: tuple[float, ...],
    weights: tuple[float, ...],
) -> MetricSummary:
    _validate(rows, probabilities, weights)
    total_weight = sum(weights)
    predictions = tuple(1 if probability >= 0.5 else 0 for probability in probabilities)
    accuracy = sum(
        weight
        for row, predicted, weight in zip(rows, predictions, weights, strict=True)
        if row.target == predicted
    ) / total_weight

    recalls: list[float] = []
    for target in (0, 1):
        denominator = sum(
            weight
            for row, weight in zip(rows, weights, strict=True)
            if row.target == target
        )
        if denominator == 0:
            recalls = []
            break
        numerator = sum(
            weight
            for row, predicted, weight in zip(rows, predictions, weights, strict=True)
            if row.target == target and predicted == target
        )
        recalls.append(numerator / denominator)
    balanced_accuracy = sum(recalls) / 2 if recalls else None

    epsilon = 1e-15
    log_loss = -sum(
        weight
        * (
            row.target * math.log(min(max(probability, epsilon), 1 - epsilon))
            + (1 - row.target)
            * math.log(min(max(1 - probability, epsilon), 1 - epsilon))
        )
        for row, probability, weight in zip(rows, probabilities, weights, strict=True)
    ) / total_weight
    brier = sum(
        weight * (probability - row.target) ** 2
        for row, probability, weight in zip(rows, probabilities, weights, strict=True)
    ) / total_weight

    bucket_rows: dict[str, list[tuple[SupervisedRow, float, float]]] = defaultdict(list)
    for row, probability, weight in zip(rows, probabilities, weights, strict=True):
        confidence = max(probability, 1.0 - probability)
        for low, high, label in _CALIBRATION_BOUNDS:
            if low <= confidence < high:
                bucket_rows[label].append((row, probability, weight))
                break

    calibration: list[dict[str, object]] = []
    ece = 0.0
    for _, _, label in _CALIBRATION_BOUNDS:
        entries = bucket_rows[label]
        if not entries:
            calibration.append(
                {
                    "label": label,
                    "row_count": 0,
                    "weight": 0.0,
                    "mean_confidence": None,
                    "accuracy": None,
                    "gap": None,
                }
            )
            continue
        bucket_weight = sum(entry[2] for entry in entries)
        mean_confidence = sum(
            max(probability, 1.0 - probability) * weight
            for _, probability, weight in entries
        ) / bucket_weight
        bucket_accuracy = sum(
            weight
            for row, probability, weight in entries
            if row.target == (1 if probability >= 0.5 else 0)
        ) / bucket_weight
        gap = abs(bucket_accuracy - mean_confidence)
        ece += (bucket_weight / total_weight) * gap
        calibration.append(
            {
                "label": label,
                "row_count": len(entries),
                "weight": bucket_weight,
                "mean_confidence": mean_confidence,
                "accuracy": bucket_accuracy,
                "gap": gap,
            }
        )

    coverage: dict[str, dict[str, float | int]] = {}
    for threshold in _COVERAGE_THRESHOLDS:
        eligible = [
            (row, probability, weight)
            for row, probability, weight in zip(rows, probabilities, weights, strict=True)
            if max(probability, 1.0 - probability) >= threshold
        ]
        eligible_weight = sum(entry[2] for entry in eligible)
        eligible_correct = sum(
            weight
            for row, probability, weight in eligible
            if row.target == (1 if probability >= 0.5 else 0)
        )
        key = f"{threshold:.2f}"
        coverage[key] = {
            "rows": len(eligible),
            "coverage": eligible_weight / total_weight,
            "accuracy": eligible_correct / eligible_weight if eligible_weight else 0.0,
        }

    return MetricSummary(
        row_count=len(rows),
        market_count=len({row.condition_id for row in rows}),
        accuracy=accuracy,
        balanced_accuracy=balanced_accuracy,
        log_loss=log_loss,
        brier_score=brier,
        ece=ece,
        calibration=tuple(calibration),
        confidence_coverage=coverage,
    )
