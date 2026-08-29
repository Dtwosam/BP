from __future__ import annotations

import pytest


def _interval(*, mean: float = 0.10, lower: float = 0.02, upper: float = 0.18):
    from bp_engine.improvement.statistics import BootstrapInterval

    return BootstrapInterval(
        mean_delta=mean,
        lower=lower,
        upper=upper,
        resamples=10_000,
        paired_markets=50,
    )


def test_positive_mean_with_non_positive_lower_bound_is_ineligible() -> None:
    from bp_engine.improvement.comparison import compare_policies

    result = compare_policies(
        economic_interval=_interval(mean=0.10, lower=0.0),
        champion_log_loss=0.50,
        challenger_log_loss=0.49,
        champion_brier=0.20,
        challenger_brier=0.19,
        independent_confirmation_present=True,
        integrity_ok=True,
    )

    assert result.promotion_eligible is False
    assert result.ineligibility_reasons == ("economic_uncertainty_not_positive",)


def test_worse_log_loss_is_ineligible() -> None:
    from bp_engine.improvement.comparison import compare_policies

    result = compare_policies(
        economic_interval=_interval(),
        champion_log_loss=0.50,
        challenger_log_loss=0.51,
        champion_brier=0.20,
        challenger_brier=0.19,
        independent_confirmation_present=True,
        integrity_ok=True,
    )

    assert result.calibration_log_loss_delta == pytest.approx(0.01)
    assert result.ineligibility_reasons == ("calibration_log_loss_worse",)


def test_worse_brier_is_ineligible() -> None:
    from bp_engine.improvement.comparison import compare_policies

    result = compare_policies(
        economic_interval=_interval(),
        champion_log_loss=0.50,
        challenger_log_loss=0.49,
        champion_brier=0.20,
        challenger_brier=0.21,
        independent_confirmation_present=True,
        integrity_ok=True,
    )

    assert result.calibration_brier_delta == pytest.approx(0.01)
    assert result.ineligibility_reasons == ("calibration_brier_worse",)


def test_missing_independent_confirmation_is_ineligible() -> None:
    from bp_engine.improvement.comparison import compare_policies

    result = compare_policies(
        economic_interval=_interval(),
        champion_log_loss=0.50,
        challenger_log_loss=0.49,
        champion_brier=0.20,
        challenger_brier=0.19,
        independent_confirmation_present=False,
        integrity_ok=True,
    )

    assert result.ineligibility_reasons == ("independent_confirmation_missing",)


def test_integrity_violation_is_ineligible() -> None:
    from bp_engine.improvement.comparison import compare_policies

    result = compare_policies(
        economic_interval=_interval(),
        champion_log_loss=0.50,
        challenger_log_loss=0.49,
        champion_brier=0.20,
        challenger_brier=0.19,
        independent_confirmation_present=True,
        integrity_ok=False,
    )

    assert result.ineligibility_reasons == ("integrity_violation",)


def test_all_frozen_promotion_gates_pass() -> None:
    from bp_engine.improvement.comparison import compare_policies

    interval = _interval(mean=0.12, lower=0.03, upper=0.20)
    result = compare_policies(
        economic_interval=interval,
        champion_log_loss=0.50,
        challenger_log_loss=0.49,
        champion_brier=0.20,
        challenger_brier=0.19,
        independent_confirmation_present=True,
        integrity_ok=True,
    )

    assert result.economic_delta == interval.mean_delta
    assert result.economic_interval == interval
    assert result.calibration_log_loss_delta == pytest.approx(-0.01)
    assert result.calibration_brier_delta == pytest.approx(-0.01)
    assert result.promotion_eligible is True
    assert result.ineligibility_reasons == ()


def test_missing_economic_interval_fails_closed() -> None:
    from bp_engine.improvement.comparison import compare_policies

    result = compare_policies(
        economic_interval=None,
        champion_log_loss=0.50,
        challenger_log_loss=0.49,
        champion_brier=0.20,
        challenger_brier=0.19,
        independent_confirmation_present=True,
        integrity_ok=True,
    )

    assert result.economic_delta is None
    assert result.promotion_eligible is False
    assert result.ineligibility_reasons == ("economic_uncertainty_not_positive",)


def test_reason_codes_are_sorted_and_deduplicated() -> None:
    from bp_engine.improvement.comparison import compare_policies

    result = compare_policies(
        economic_interval=_interval(mean=-0.1, lower=-0.2, upper=0.0),
        champion_log_loss=0.50,
        challenger_log_loss=0.51,
        champion_brier=0.20,
        challenger_brier=0.21,
        independent_confirmation_present=False,
        integrity_ok=False,
    )

    assert result.ineligibility_reasons == tuple(sorted(set(result.ineligibility_reasons)))
    assert result.ineligibility_reasons == (
        "calibration_brier_worse",
        "calibration_log_loss_worse",
        "economic_uncertainty_not_positive",
        "independent_confirmation_missing",
        "integrity_violation",
    )
