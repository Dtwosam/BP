from __future__ import annotations

import pytest

from bp_engine.backtesting.uncertainty import wilson_accuracy_interval


def test_wilson_interval_rejects_zero_total() -> None:
    with pytest.raises(ValueError, match="total"):
        wilson_accuracy_interval(0, 0)


@pytest.mark.parametrize(("correct", "total"), [(0, 10), (10, 10)])
def test_wilson_interval_stays_within_probability_bounds(
    correct: int, total: int
) -> None:
    low, high = wilson_accuracy_interval(correct, total)

    assert 0.0 <= low <= high <= 1.0


def test_wilson_interval_contains_observed_accuracy() -> None:
    low, high = wilson_accuracy_interval(8, 10)

    assert low <= 0.8 <= high


def test_wilson_interval_rejects_impossible_correct_count() -> None:
    with pytest.raises(ValueError, match="correct"):
        wilson_accuracy_interval(11, 10)
