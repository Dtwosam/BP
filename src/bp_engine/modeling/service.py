from __future__ import annotations

from collections.abc import Mapping

from bp_engine.modeling.models import ModelEvaluation, SupervisedRow


def select_validation_champion(evaluations: Mapping[str, ModelEvaluation]) -> str:
    if not evaluations:
        raise ValueError("evaluations must not be empty")
    return min(
        evaluations,
        key=lambda name: (
            evaluations[name].validation.log_loss,
            evaluations[name].validation.brier_score,
            name,
        ),
    )


def xgboost_promotion_eligible(evaluations: Mapping[str, ModelEvaluation]) -> bool:
    required = {"prior", "market_price", "logistic", "xgboost"}
    if not required <= set(evaluations):
        return False
    xgb = evaluations["xgboost"]
    simple_names = ("prior", "market_price", "logistic")
    if not all(
        xgb.validation.log_loss < evaluations[name].validation.log_loss
        and xgb.validation.brier_score < evaluations[name].validation.brier_score
        for name in simple_names
    ):
        return False
    simple_champion = min(
        simple_names,
        key=lambda name: (
            evaluations[name].validation.log_loss,
            evaluations[name].validation.brier_score,
            name,
        ),
    )
    simple_test = evaluations[simple_champion].test
    return (
        xgb.test.log_loss < simple_test.log_loss
        and xgb.test.brier_score <= simple_test.brier_score
    )


def gross_execution_diagnostic(
    rows: tuple[SupervisedRow, ...],
    probabilities: tuple[float, ...],
) -> dict[str, float | int]:
    if len(rows) != len(probabilities):
        raise ValueError("rows and probabilities must have equal length")
    gross_pnl = 0.0
    eligible = 0
    for row, probability in zip(rows, probabilities, strict=True):
        predicted = 1 if probability >= 0.5 else 0
        ask_key = "pm_up_best_ask" if predicted == 1 else "pm_down_best_ask"
        ask = row.predictors.get(ask_key)
        if ask is None:
            continue
        ask_value = float(ask)
        if not 0 <= ask_value <= 1:
            raise ValueError(f"{ask_key} must be within [0, 1]")
        payout = 1.0 if row.target == predicted else 0.0
        gross_pnl += payout - ask_value
        eligible += 1
    total = len(rows)
    return {
        "eligible_rows": eligible,
        "total_rows": total,
        "coverage": eligible / total if total else 0.0,
        "gross_execution_pnl_before_costs": gross_pnl,
        "mean_gross_pnl_per_executed_share": gross_pnl / eligible if eligible else 0.0,
    }
