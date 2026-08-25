from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from bp_engine.backtesting.regimes import (
    regime_metrics,
    training_volatility_threshold,
    utc_session_regime,
    volatility_regime,
)

from bp_engine.modeling.models import SupervisedRow


def _row(
    condition_id: str,
    *,
    hour: int,
    target: int,
    volatility: float | None,
    offset_seconds: int = 60,
    executable: bool = True,
) -> SupervisedRow:
    start = datetime(2026, 8, 24, hour, tzinfo=UTC)
    return SupervisedRow(
        condition_id=condition_id,
        slug=f"market-{condition_id}",
        horizon_seconds=300,
        market_start_at=start,
        market_end_at=start + timedelta(seconds=300),
        feature_at=start + timedelta(seconds=offset_seconds),
        feature_offset_seconds=offset_seconds,
        predictors={
            "coinbase_realized_vol_15m": volatility,
            "pm_up_best_ask": 0.60 if executable else None,
            "pm_down_best_ask": 0.40 if executable else None,
            "missing__pm_up_book_missing": 0.0 if executable else 1.0,
            "missing__pm_up_book_stale": 0.0,
            "missing__pm_down_book_missing": 0.0 if executable else 1.0,
            "missing__pm_down_book_stale": 0.0,
        },
        target=target,
        feature_hash="a" * 64,
        input_fingerprint="b" * 64,
    )


@pytest.mark.parametrize(
    ("hour", "expected"),
    [
        (0, "00-06"),
        (5, "00-06"),
        (6, "06-12"),
        (11, "06-12"),
        (12, "12-18"),
        (17, "12-18"),
        (18, "18-24"),
        (23, "18-24"),
    ],
)
def test_utc_session_regime_boundaries(hour: int, expected: str) -> None:
    assert utc_session_regime(
        _row(f"h-{hour}", hour=hour, target=hour % 2, volatility=0.2)
    ) == expected


def test_volatility_threshold_uses_training_rows_only() -> None:
    train = (
        _row("train-a", hour=0, target=0, volatility=0.10),
        _row("train-b", hour=1, target=1, volatility=0.30),
        _row("train-c", hour=2, target=0, volatility=None),
        _row("other-offset", hour=3, target=1, volatility=99.0, offset_seconds=120),
    )
    unrelated_test = (
        _row("test-a", hour=4, target=0, volatility=1000.0),
        _row("test-b", hour=5, target=1, volatility=2000.0),
    )

    first = training_volatility_threshold(train, 60)
    _mutated_test = tuple(
        _row(
            row.condition_id,
            hour=row.market_start_at.hour,
            target=1 - row.target,
            volatility=0.000001,
        )
        for row in unrelated_test
    )
    second = training_volatility_threshold(train, 60)

    assert first == pytest.approx(0.20)
    assert second == pytest.approx(first)


def test_volatility_regime_marks_null_unknown() -> None:
    assert volatility_regime(
        _row("low", hour=0, target=0, volatility=0.10), 0.20
    ) == "low"
    assert volatility_regime(
        _row("high", hour=0, target=1, volatility=0.30), 0.20
    ) == "high"
    assert volatility_regime(
        _row("unknown", hour=0, target=1, volatility=None), 0.20
    ) == "unknown"
    assert volatility_regime(
        _row("no-threshold", hour=0, target=1, volatility=0.30), None
    ) == "unknown"


def test_regime_metrics_include_market_counts_and_one_class_semantics() -> None:
    rows = (
        _row("a", hour=1, target=1, volatility=0.10, executable=True),
        _row("b", hour=2, target=1, volatility=0.30, executable=False),
        _row("c", hour=7, target=0, volatility=0.40, executable=True),
    )
    probabilities = (0.80, 0.70, 0.20)

    result = regime_metrics(rows, probabilities, volatility_threshold=0.20)

    assert result["utc_session"]["00-06"]["market_count"] == 2
    assert result["utc_session"]["00-06"]["metrics"]["balanced_accuracy"] is None
    assert result["utc_session"]["06-12"]["market_count"] == 1
    assert result["volatility"]["low"]["market_count"] == 1
    assert result["volatility"]["high"]["market_count"] == 2
    assert result["execution_availability"]["executable"]["market_count"] == 2
    assert result["execution_availability"]["unavailable"]["market_count"] == 1
