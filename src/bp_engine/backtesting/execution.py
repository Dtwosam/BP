from __future__ import annotations

import math

from bp_engine.modeling.models import SupervisedRow


def _validate_probability(probability: float) -> None:
    if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
        raise ValueError("probability must be finite and within [0, 1]")


def _flag_is_clear(row: SupervisedRow, key: str) -> bool:
    if key not in row.predictors:
        return False
    value = row.predictors[key]
    if value is None:
        return False
    numeric = float(value)
    return math.isfinite(numeric) and numeric == 0.0


def selected_observed_ask(row: SupervisedRow, probability: float) -> float | None:
    """Return the observed ask for the predicted side, or None when no fill is provable."""

    _validate_probability(probability)
    prefix = "pm_up" if probability >= 0.5 else "pm_down"
    if not _flag_is_clear(row, f"missing__{prefix}_book_missing"):
        return None
    if not _flag_is_clear(row, f"missing__{prefix}_book_stale"):
        return None

    value = row.predictors.get(f"{prefix}_best_ask")
    if value is None:
        return None
    ask = float(value)
    if not math.isfinite(ask) or not 0.0 < ask <= 1.0:
        return None
    return ask


def execution_diagnostic(
    rows: tuple[SupervisedRow, ...], probabilities: tuple[float, ...]
) -> dict[str, float | int | None]:
    if not rows:
        raise ValueError("rows must not be empty")
    if len(rows) != len(probabilities):
        raise ValueError("rows and probabilities must have equal length")

    asks: list[float] = []
    gross_pnl = 0.0
    correct = 0
    for row, probability in zip(rows, probabilities, strict=True):
        _validate_probability(probability)
        ask = selected_observed_ask(row, probability)
        if ask is None:
            continue
        predicted = 1 if probability >= 0.5 else 0
        is_correct = row.target == predicted
        payout = 1.0 if is_correct else 0.0
        asks.append(ask)
        gross_pnl += payout - ask
        correct += int(is_correct)

    prediction_markets = len(rows)
    executable_markets = len(asks)
    unavailable = prediction_markets - executable_markets
    return {
        "prediction_markets": prediction_markets,
        "executable_markets": executable_markets,
        "unavailable_no_fill_markets": unavailable,
        "execution_coverage": executable_markets / prediction_markets,
        "average_observed_ask": (
            sum(asks) / executable_markets if executable_markets else None
        ),
        "correct_executed_trades": correct,
        "gross_execution_pnl_before_costs": gross_pnl,
        "mean_gross_pnl_per_executed_share": (
            gross_pnl / executable_markets if executable_markets else None
        ),
    }
