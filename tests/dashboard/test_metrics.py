from decimal import Decimal

import pytest
from bp_engine.dashboard.metrics import build_performance


def test_zero_evaluations_is_pending_not_zero_performance() -> None:
    result = build_performance([])

    assert result.status == "pending"
    assert result.evaluated_count == 0
    assert result.accuracy is None
    assert result.calibrated_brier is None
    assert result.calibrated_log_loss is None
    assert result.research_hypothetical_assumed_cost_pnl is None
    assert result.paper_pnl_status == "unavailable_until_phase_12"
    assert result.horizons == ()
    assert all(bucket.count == 0 for bucket in result.calibration_buckets)


def test_performance_aggregates_exact_evaluations_and_horizons() -> None:
    rows = [
        {
            "horizon_seconds": 300,
            "calibrated_probability": Decimal("0.800000000000000000"),
            "official_target": 1,
            "correct": True,
            "calibrated_brier": Decimal("0.040000000000000000"),
            "calibrated_log_loss": Decimal("0.223143551314209760"),
            "hypothetical_assumed_cost_pnl": Decimal("0.12"),
        },
        {
            "horizon_seconds": 900,
            "calibrated_probability": Decimal("0.300000000000000000"),
            "official_target": 0,
            "correct": True,
            "calibrated_brier": Decimal("0.090000000000000000"),
            "calibrated_log_loss": Decimal("0.356674943938732379"),
            "hypothetical_assumed_cost_pnl": None,
        },
    ]

    result = build_performance(rows)

    assert result.status == "evaluated"
    assert result.evaluated_count == 2
    assert result.accuracy == Decimal("1")
    assert result.calibrated_brier == Decimal("0.065000000000000000")
    assert result.calibrated_log_loss == Decimal("0.2899092476264710695")
    assert [item.horizon_seconds for item in result.horizons] == [300, 900]
    assert [item.evaluated_count for item in result.horizons] == [1, 1]
    assert result.research_hypothetical_assumed_cost_pnl == Decimal("0.12")
    assert result.paper_pnl_status == "unavailable_until_phase_12"


def test_calibration_buckets_are_fixed_and_include_probability_one() -> None:
    rows = [
        {
            "horizon_seconds": 300,
            "calibrated_probability": Decimal("0.05"),
            "official_target": 0,
            "correct": True,
            "calibrated_brier": Decimal("0.0025"),
            "calibrated_log_loss": Decimal("0.05129329438755058"),
            "hypothetical_assumed_cost_pnl": None,
        },
        {
            "horizon_seconds": 300,
            "calibrated_probability": Decimal("0.15"),
            "official_target": 1,
            "correct": False,
            "calibrated_brier": Decimal("0.7225"),
            "calibrated_log_loss": Decimal("1.8971199848858813"),
            "hypothetical_assumed_cost_pnl": None,
        },
        {
            "horizon_seconds": 900,
            "calibrated_probability": Decimal("1"),
            "official_target": 1,
            "correct": True,
            "calibrated_brier": Decimal("0"),
            "calibrated_log_loss": Decimal("0"),
            "hypothetical_assumed_cost_pnl": None,
        },
    ]

    buckets = build_performance(rows).calibration_buckets

    assert len(buckets) == 10
    assert buckets[0].lower_bound == Decimal("0.0")
    assert buckets[0].upper_bound == Decimal("0.1")
    assert buckets[0].count == 1
    assert buckets[1].count == 1
    assert buckets[9].count == 1
    assert buckets[9].mean_probability == Decimal("1")
    assert buckets[9].observed_up_frequency == Decimal("1")


def test_performance_rejects_invalid_probability_or_horizon() -> None:
    base = {
        "horizon_seconds": 300,
        "calibrated_probability": Decimal("0.5"),
        "official_target": 1,
        "correct": True,
        "calibrated_brier": Decimal("0.25"),
        "calibrated_log_loss": Decimal("0.6931471805599453"),
        "hypothetical_assumed_cost_pnl": None,
    }

    with pytest.raises(ValueError, match="probability"):
        build_performance([{**base, "calibrated_probability": Decimal("1.1")}])
    with pytest.raises(ValueError, match="horizon"):
        build_performance([{**base, "horizon_seconds": 600}])


def test_decimal_values_serialize_as_strings() -> None:
    row = {
        "horizon_seconds": 300,
        "calibrated_probability": Decimal("0.8"),
        "official_target": 1,
        "correct": True,
        "calibrated_brier": Decimal("0.04"),
        "calibrated_log_loss": Decimal("0.22"),
        "hypothetical_assumed_cost_pnl": Decimal("0.10"),
    }

    payload = build_performance([row]).model_dump(mode="json")

    assert payload["accuracy"] == "1"
    assert payload["calibrated_brier"] == "0.04"
    assert payload["research_hypothetical_assumed_cost_pnl"] == "0.10"
