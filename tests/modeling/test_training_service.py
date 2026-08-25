from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bp_engine.modeling.models import MetricSummary, ModelEvaluation, SupervisedRow
from bp_engine.modeling.service import (
    gross_execution_diagnostic,
    select_validation_champion,
    xgboost_promotion_eligible,
)


def _metric(log_loss: float, brier: float, balanced: float = 0.6) -> MetricSummary:
    return MetricSummary(
        row_count=10,
        market_count=10,
        accuracy=0.6,
        balanced_accuracy=balanced,
        log_loss=log_loss,
        brier_score=brier,
        ece=0.05,
        calibration=(),
        confidence_coverage={},
    )


def _evaluation(
    family: str,
    validation_log_loss: float,
    validation_brier: float,
    test_log_loss: float,
    test_brier: float,
) -> ModelEvaluation:
    return ModelEvaluation(
        family=family,
        config={},
        validation=_metric(validation_log_loss, validation_brier),
        test=_metric(test_log_loss, test_brier),
    )


def test_validation_champion_is_not_rewritten_by_better_test_result() -> None:
    evaluations = {
        "logistic": _evaluation("logistic", 0.40, 0.18, 0.50, 0.24),
        "xgboost": _evaluation("xgboost", 0.42, 0.19, 0.30, 0.12),
    }

    assert select_validation_champion(evaluations) == "logistic"
    assert min(evaluations, key=lambda name: evaluations[name].test.log_loss) == "xgboost"


def test_xgboost_promotion_requires_validation_and_test_confirmation() -> None:
    eligible = {
        "prior": _evaluation("prior", 0.69, 0.25, 0.68, 0.24),
        "market_price": _evaluation("market_price", 0.55, 0.20, 0.56, 0.21),
        "logistic": _evaluation("logistic", 0.50, 0.18, 0.51, 0.19),
        "xgboost": _evaluation("xgboost", 0.45, 0.16, 0.48, 0.18),
    }
    assert xgboost_promotion_eligible(eligible) is True

    not_confirmed = dict(eligible)
    not_confirmed["xgboost"] = _evaluation("xgboost", 0.45, 0.16, 0.48, 0.20)
    assert xgboost_promotion_eligible(not_confirmed) is False


def _row(index: int, target: int, up_ask: float | None, down_ask: float | None) -> SupervisedRow:
    start = datetime(2026, 8, 24, tzinfo=UTC) + timedelta(minutes=5 * index)
    return SupervisedRow(
        condition_id=f"condition-{index}",
        slug=f"btc-updown-5m-{index}",
        horizon_seconds=300,
        market_start_at=start,
        market_end_at=start + timedelta(minutes=5),
        feature_at=start + timedelta(minutes=1),
        feature_offset_seconds=60,
        predictors={"pm_down_best_ask": down_ask, "pm_up_best_ask": up_ask},
        target=target,
        feature_hash=f"{index}" * 64,
        input_fingerprint=f"{index + 1}" * 64,
    )


def test_gross_execution_diagnostic_uses_only_observed_predicted_side_ask() -> None:
    rows = (
        _row(0, 1, 0.60, 0.42),
        _row(1, 0, 0.35, 0.70),
        _row(2, 1, None, 0.40),
    )
    result = gross_execution_diagnostic(rows, (0.70, 0.20, 0.80))

    assert result["eligible_rows"] == 2
    assert result["total_rows"] == 3
    assert result["coverage"] == pytest.approx(2 / 3)
    assert result["gross_execution_pnl_before_costs"] == pytest.approx(0.70)
    assert result["mean_gross_pnl_per_executed_share"] == pytest.approx(0.35)
