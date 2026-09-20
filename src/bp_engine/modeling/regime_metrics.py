from __future__ import annotations

from typing import Any

from bp_engine.modeling.metrics import evaluate_probabilities
from bp_engine.modeling.models import SupervisedRow
from bp_engine.modeling.split import equal_market_weights

REGIME_NAMES = ("bull", "bear", "sideways_mixed")


def regime_label_from_predictors(
    predictors: dict[str, float | None],
) -> str:
    flags = {
        "bull": predictors.get("regime_bull"),
        "bear": predictors.get("regime_bear"),
        "sideways_mixed": predictors.get("regime_sideways_mixed"),
    }
    if any(value is None for value in flags.values()):
        return "unknown"
    active = tuple(name for name, value in flags.items() if float(value) == 1.0)
    if len(active) != 1:
        raise ValueError("V4 regime flags must contain exactly one active regime")
    return active[0]


def evaluate_probabilities_by_regime(
    rows: tuple[SupervisedRow, ...],
    probabilities: tuple[float, ...],
) -> dict[str, dict[str, Any]]:
    if len(rows) != len(probabilities):
        raise ValueError("rows and probabilities must have equal length")

    grouped: dict[str, list[tuple[SupervisedRow, float]]] = {
        name: [] for name in (*REGIME_NAMES, "unknown")
    }
    for row, probability in zip(rows, probabilities, strict=True):
        grouped[regime_label_from_predictors(row.predictors)].append(
            (row, probability)
        )

    report: dict[str, dict[str, Any]] = {}
    for name, entries in grouped.items():
        if not entries:
            report[name] = {
                "market_count": 0,
                "up_outcomes": 0,
                "down_outcomes": 0,
                "prediction_up_count": 0,
                "prediction_down_count": 0,
                "metrics": None,
            }
            continue
        subset_rows = tuple(row for row, _ in entries)
        subset_probabilities = tuple(probability for _, probability in entries)
        metrics = evaluate_probabilities(
            subset_rows,
            subset_probabilities,
            tuple(equal_market_weights(subset_rows)),
        )
        prediction_up = sum(probability >= 0.5 for probability in subset_probabilities)
        report[name] = {
            "market_count": metrics.market_count,
            "up_outcomes": sum(row.target == 1 for row in subset_rows),
            "down_outcomes": sum(row.target == 0 for row in subset_rows),
            "prediction_up_count": prediction_up,
            "prediction_down_count": len(subset_rows) - prediction_up,
            "metrics": {
                "accuracy": metrics.accuracy,
                "balanced_accuracy": metrics.balanced_accuracy,
                "log_loss": metrics.log_loss,
                "brier_score": metrics.brier_score,
                "ece": metrics.ece,
            },
        }
    return report
