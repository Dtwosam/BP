from __future__ import annotations

import math

import pytest


def test_paired_bootstrap_is_order_invariant_and_positive() -> None:
    from bp_engine.improvement.statistics import paired_bootstrap_mean_delta

    pairs = (
        ("c1", -0.10, 0.20),
        ("c2", 0.00, 0.30),
        ("c3", -0.20, 0.10),
        ("c4", 0.05, 0.25),
    )

    first = paired_bootstrap_mean_delta(pairs, seed=1234)
    second = paired_bootstrap_mean_delta(tuple(reversed(pairs)), seed=1234)

    assert first == second
    assert first.mean_delta > 0
    assert first.resamples == 10_000
    assert first.paired_markets == 4


def test_paired_bootstrap_rejects_empty_pairs() -> None:
    from bp_engine.improvement.statistics import paired_bootstrap_mean_delta

    with pytest.raises(ValueError, match="paired market"):
        paired_bootstrap_mean_delta((), seed=1234)


def test_paired_bootstrap_rejects_non_finite_pairs() -> None:
    from bp_engine.improvement.statistics import paired_bootstrap_mean_delta

    pairs = (("c1", 0.0, math.nan),)
    with pytest.raises(ValueError, match="finite"):
        paired_bootstrap_mean_delta(pairs, seed=1234)


def test_paired_bootstrap_rejects_duplicate_condition_ids() -> None:
    from bp_engine.improvement.statistics import paired_bootstrap_mean_delta

    pairs = (("c1", 0.0, 0.1), ("c1", 0.2, 0.3))
    with pytest.raises(ValueError, match="condition"):
        paired_bootstrap_mean_delta(pairs, seed=1234)


def test_max_drawdown_uses_cumulative_realized_pnl_path() -> None:
    from bp_engine.improvement.statistics import max_drawdown

    assert max_drawdown((1.0, -0.4, -0.8, 0.3, 0.2)) == pytest.approx(1.2)
    assert max_drawdown((0.1, 0.2, 0.3)) == pytest.approx(0.0)


def test_max_losing_streak_counts_consecutive_negative_outcomes() -> None:
    from bp_engine.improvement.statistics import max_losing_streak

    assert max_losing_streak((0.1, -0.2, -0.3, 0.0, -0.4, -0.5, -0.6)) == 3
    assert max_losing_streak((0.0, 0.1, 0.2)) == 0


def test_sequential_pnl_diagnostics_reject_non_finite_values() -> None:
    from bp_engine.improvement.statistics import max_drawdown, max_losing_streak

    with pytest.raises(ValueError, match="finite"):
        max_drawdown((0.1, math.inf))
    with pytest.raises(ValueError, match="finite"):
        max_losing_streak((0.1, math.nan))
