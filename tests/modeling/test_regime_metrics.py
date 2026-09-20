from datetime import UTC, datetime, timedelta

import pytest

from bp_engine.modeling.models import SupervisedRow
from bp_engine.modeling.regime_metrics import (
    evaluate_probabilities_by_regime,
    regime_label_from_predictors,
)

START = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def _row(name: str, *, target: int, regime: str) -> SupervisedRow:
    flags = {
        "regime_bull": 1.0 if regime == "bull" else 0.0,
        "regime_bear": 1.0 if regime == "bear" else 0.0,
        "regime_sideways_mixed": 1.0 if regime == "sideways_mixed" else 0.0,
    }
    return SupervisedRow(
        condition_id=name,
        slug=name,
        horizon_seconds=300,
        market_start_at=START,
        market_end_at=START + timedelta(seconds=300),
        feature_at=START + timedelta(seconds=240),
        feature_offset_seconds=240,
        predictors=flags,
        target=target,
        feature_hash="a" * 64,
        input_fingerprint="b" * 64,
    )


def test_regime_label_requires_exactly_one_active_regime() -> None:
    assert regime_label_from_predictors(
        {"regime_bull": 1.0, "regime_bear": 0.0, "regime_sideways_mixed": 0.0}
    ) == "bull"
    assert regime_label_from_predictors(
        {"regime_bull": None, "regime_bear": None, "regime_sideways_mixed": None}
    ) == "unknown"
    with pytest.raises(ValueError, match="exactly one"):
        regime_label_from_predictors(
            {"regime_bull": 1.0, "regime_bear": 1.0, "regime_sideways_mixed": 0.0}
        )


def test_regime_report_prevents_overall_metrics_from_hiding_weak_regime() -> None:
    rows = (
        _row("bull-1", target=1, regime="bull"),
        _row("bull-2", target=1, regime="bull"),
        _row("bear-1", target=0, regime="bear"),
        _row("bear-2", target=0, regime="bear"),
        _row("sideways-1", target=1, regime="sideways_mixed"),
        _row("sideways-2", target=0, regime="sideways_mixed"),
    )
    probabilities = (0.9, 0.8, 0.9, 0.8, 0.6, 0.4)

    report = evaluate_probabilities_by_regime(rows, probabilities)

    assert report["bull"]["market_count"] == 2
    assert report["bull"]["metrics"]["accuracy"] == 1.0
    assert report["bear"]["market_count"] == 2
    assert report["bear"]["metrics"]["accuracy"] == 0.0
    assert report["sideways_mixed"]["market_count"] == 2
    assert report["sideways_mixed"]["metrics"]["accuracy"] == 1.0
    assert report["unknown"]["market_count"] == 0
