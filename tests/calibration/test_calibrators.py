from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta

import pytest

from bp_engine.modeling.models import SupervisedRow


def _rows(targets: tuple[int, ...]) -> tuple[SupervisedRow, ...]:
    start = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
    result = []
    for index, target in enumerate(targets):
        market_start = start + timedelta(minutes=5 * index)
        result.append(
            SupervisedRow(
                condition_id=f"condition-{index}",
                slug=f"btc-updown-5m-{index}",
                horizon_seconds=300,
                market_start_at=market_start,
                market_end_at=market_start + timedelta(minutes=5),
                feature_at=market_start + timedelta(minutes=4),
                feature_offset_seconds=240,
                predictors={"pm_up_price": 0.5},
                target=target,
                feature_hash=f"feature-{index}",
                input_fingerprint=f"input-{index}",
            )
        )
    return tuple(result)


def _module():
    return importlib.import_module("bp_engine.calibration.calibrators")


def test_identity_clips_probabilities() -> None:
    module = _module()
    model = module.IdentityCalibrator()

    assert model.predict((0.0, 0.25, 1.0)) == pytest.approx(
        (1e-6, 0.25, 0.999999)
    )


def test_platt_fit_is_deterministic_and_monotone() -> None:
    module = _module()
    rows = _rows((0, 0, 1, 1))
    probabilities = (0.2, 0.4, 0.6, 0.8)
    weights = (1.0, 1.0, 1.0, 1.0)

    left = module.PlattCalibrator().fit(rows, probabilities, weights)
    right = module.PlattCalibrator().fit(rows, probabilities, weights)

    assert left == right
    assert left.method == "platt"
    assert left.coefficient is not None
    assert left.coefficient > 0
    predicted = module.PlattCalibrator.from_fit(left).predict(probabilities)
    assert tuple(sorted(predicted)) == predicted


def test_platt_rejects_non_monotone_fit() -> None:
    module = _module()
    rows = _rows((1, 1, 0, 0))

    with pytest.raises(module.CalibrationRejected, match="positive"):
        module.PlattCalibrator().fit(
            rows,
            (0.2, 0.4, 0.6, 0.8),
            (1.0, 1.0, 1.0, 1.0),
        )


def test_platt_promotes_only_when_both_validation_metrics_improve(monkeypatch) -> None:
    module = _module()
    rows = _rows((0, 0, 1, 1))
    train_probabilities = (0.2, 0.4, 0.6, 0.8)
    validation_probabilities = (0.3, 0.45, 0.55, 0.7)

    real_evaluate = module.evaluate_probabilities
    calls = []

    def fake_evaluate(eval_rows, probabilities, weights):
        calls.append(tuple(probabilities))
        result = real_evaluate(eval_rows, probabilities, weights)
        if len(calls) == 2:
            # Challenger improves log loss but not Brier: identity must remain selected.
            return result.__class__(
                row_count=result.row_count,
                market_count=result.market_count,
                accuracy=result.accuracy,
                balanced_accuracy=result.balanced_accuracy,
                log_loss=max(0.0, calls_metrics[0].log_loss - 0.01),
                brier_score=calls_metrics[0].brier_score + 0.01,
                ece=result.ece,
                calibration=result.calibration,
                confidence_coverage=result.confidence_coverage,
            )
        calls_metrics.append(result)
        return result

    calls_metrics = []
    monkeypatch.setattr(module, "evaluate_probabilities", fake_evaluate)
    selection = module.select_calibrator(
        rows,
        train_probabilities,
        rows,
        validation_probabilities,
    )

    assert selection.method == "identity"
    assert selection.fit.method == "identity"
    assert tuple(candidate.method for candidate in selection.candidates) == (
        "identity",
        "platt",
    )
