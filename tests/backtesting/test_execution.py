from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from bp_engine.backtesting.execution import execution_diagnostic

from bp_engine.modeling.models import SupervisedRow


def _row(
    condition_id: str,
    *,
    target: int,
    up_ask: float | None = 0.60,
    down_ask: float | None = 0.40,
    up_missing: bool = False,
    up_stale: bool = False,
    down_missing: bool = False,
    down_stale: bool = False,
) -> SupervisedRow:
    start = datetime(2026, 8, 24, tzinfo=UTC)
    predictors: dict[str, float | None] = {
        "pm_up_best_ask": up_ask,
        "pm_down_best_ask": down_ask,
        "pm_up_mid": 0.50,
        "pm_down_mid": 0.50,
        "pm_up_price": 0.50,
        "pm_down_price": 0.50,
        "missing__pm_up_book_missing": float(up_missing),
        "missing__pm_up_book_stale": float(up_stale),
        "missing__pm_down_book_missing": float(down_missing),
        "missing__pm_down_book_stale": float(down_stale),
    }
    return SupervisedRow(
        condition_id=condition_id,
        slug=f"market-{condition_id}",
        horizon_seconds=300,
        market_start_at=start,
        market_end_at=start + timedelta(seconds=300),
        feature_at=start + timedelta(seconds=60),
        feature_offset_seconds=60,
        predictors=predictors,
        target=target,
        feature_hash="a" * 64,
        input_fingerprint="b" * 64,
    )


def test_execution_uses_only_observed_selected_side_best_ask() -> None:
    rows = (
        _row("up", target=1, up_ask=0.70, down_ask=0.20),
        _row("down", target=0, up_ask=0.80, down_ask=0.25),
    )

    result = execution_diagnostic(rows, (0.80, 0.20))

    assert result["prediction_markets"] == 2
    assert result["executable_markets"] == 2
    assert result["unavailable_no_fill_markets"] == 0
    assert result["execution_coverage"] == pytest.approx(1.0)
    assert result["average_observed_ask"] == pytest.approx(0.475)
    assert result["correct_executed_trades"] == 2
    assert result["gross_execution_pnl_before_costs"] == pytest.approx(1.05)
    assert result["mean_gross_pnl_per_executed_share"] == pytest.approx(0.525)


def test_unselected_side_book_flags_do_not_block_execution() -> None:
    rows = (
        _row("up", target=1, up_ask=0.60, down_missing=True, down_stale=True),
        _row("down", target=0, down_ask=0.40, up_missing=True, up_stale=True),
    )

    result = execution_diagnostic(rows, (0.90, 0.10))

    assert result["executable_markets"] == 2
    assert result["execution_coverage"] == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("row", "probability"),
    [
        (_row("up-missing", target=1, up_missing=True), 0.80),
        (_row("up-stale", target=1, up_stale=True), 0.80),
        (_row("up-null", target=1, up_ask=None), 0.80),
        (_row("up-zero", target=1, up_ask=0.0), 0.80),
        (_row("up-large", target=1, up_ask=1.01), 0.80),
        (_row("down-missing", target=0, down_missing=True), 0.20),
        (_row("down-stale", target=0, down_stale=True), 0.20),
        (_row("down-null", target=0, down_ask=None), 0.20),
    ],
)
def test_unusable_selected_side_is_no_fill_without_price_fallback(
    row: SupervisedRow,
    probability: float,
) -> None:
    result = execution_diagnostic((row,), (probability,))

    assert result["prediction_markets"] == 1
    assert result["executable_markets"] == 0
    assert result["unavailable_no_fill_markets"] == 1
    assert result["execution_coverage"] == 0.0
    assert result["average_observed_ask"] is None
    assert result["gross_execution_pnl_before_costs"] == 0.0
