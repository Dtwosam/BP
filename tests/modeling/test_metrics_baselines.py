from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from bp_engine.modeling.baselines import MarketPriceBaseline, PriorBaseline
from bp_engine.modeling.metrics import evaluate_probabilities
from bp_engine.modeling.models import SupervisedRow


def _row(index: int, target: int, pm_up_price: float | None) -> SupervisedRow:
    start = datetime(2026, 8, 24, tzinfo=UTC) + timedelta(minutes=5 * index)
    return SupervisedRow(
        condition_id=f"condition-{index}",
        slug=f"btc-updown-5m-{index}",
        horizon_seconds=300,
        market_start_at=start,
        market_end_at=start + timedelta(minutes=5),
        feature_at=start + timedelta(minutes=1),
        feature_offset_seconds=60,
        predictors={"pm_up_price": pm_up_price},
        target=target,
        feature_hash=f"{index}" * 64,
        input_fingerprint=f"{index + 1}" * 64,
    )


def test_probability_metrics_are_weighted_and_calibrated() -> None:
    rows = tuple(
        _row(index, target, price)
        for index, (target, price) in enumerate(
            ((1, 0.9), (0, 0.2), (1, 0.6), (0, 0.8))
        )
    )
    probabilities = (0.9, 0.2, 0.6, 0.8)
    summary = evaluate_probabilities(rows, probabilities, (1.0, 1.0, 1.0, 1.0))

    expected_log_loss = -sum(
        (
            math.log(probability)
            if row.target == 1
            else math.log(1.0 - probability)
        )
        for row, probability in zip(rows, probabilities, strict=True)
    ) / 4
    assert summary.accuracy == pytest.approx(0.75)
    assert summary.balanced_accuracy == pytest.approx(0.75)
    assert summary.brier_score == pytest.approx(0.2125)
    assert summary.log_loss == pytest.approx(expected_log_loss)
    assert 0.0 <= summary.ece <= 1.0
    assert summary.row_count == 4
    assert summary.market_count == 4
    labels = {bucket["label"] for bucket in summary.calibration}
    assert {"50-55", "55-60", "60-65", "65-70", "70-75", "75-80", "80-85", "85-90", "90+"} <= labels
    assert summary.confidence_coverage["0.80"]["coverage"] == pytest.approx(0.75)


def test_prior_baseline_uses_only_supplied_training_weights() -> None:
    rows = (_row(0, 1, None), _row(1, 0, None))
    baseline = PriorBaseline()
    baseline.fit(rows, (3.0, 1.0))

    assert baseline.probability == pytest.approx(0.75)
    assert baseline.predict_proba(rows) == pytest.approx((0.75, 0.75))


def test_market_price_baseline_uses_training_prior_only_as_missing_fallback() -> None:
    rows = (_row(0, 1, 0.9), _row(1, 0, None), _row(2, 1, 1.0))
    baseline = MarketPriceBaseline(fallback_probability=0.4)

    probabilities = baseline.predict_proba(rows)
    assert probabilities[0] == pytest.approx(0.9)
    assert probabilities[1] == pytest.approx(0.4)
    assert probabilities[2] == pytest.approx(1.0 - 1e-6)
    assert baseline.last_fallback_count == 1


def test_metrics_reject_non_finite_or_out_of_range_probabilities() -> None:
    rows = (_row(0, 1, 0.5),)
    with pytest.raises(ValueError, match="probability"):
        evaluate_probabilities(rows, (float("nan"),), (1.0,))
    with pytest.raises(ValueError, match="probability"):
        evaluate_probabilities(rows, (1.1,), (1.0,))
