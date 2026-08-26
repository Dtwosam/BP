from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta

import pytest

from bp_engine.modeling.models import SupervisedRow


def _module():
    return importlib.import_module("bp_engine.calibration.edge")


def _row(
    *,
    condition_id: str = "condition-1",
    target: int = 1,
    pm_up_price: float | None = 0.70,
    up_ask: float | None = 0.60,
    up_bid: float | None = 0.58,
    down_ask: float | None = 0.42,
    down_bid: float | None = 0.40,
    up_missing: float = 0.0,
    up_stale: float = 0.0,
    down_missing: float = 0.0,
    down_stale: float = 0.0,
) -> SupervisedRow:
    start = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
    return SupervisedRow(
        condition_id=condition_id,
        slug=f"btc-updown-5m-{condition_id}",
        horizon_seconds=300,
        market_start_at=start,
        market_end_at=start + timedelta(minutes=5),
        feature_at=start + timedelta(minutes=4),
        feature_offset_seconds=240,
        predictors={
            "pm_up_price": pm_up_price,
            "pm_up_best_ask": up_ask,
            "pm_up_best_bid": up_bid,
            "pm_down_best_ask": down_ask,
            "pm_down_best_bid": down_bid,
            "missing__pm_up_book_missing": up_missing,
            "missing__pm_up_book_stale": up_stale,
            "missing__pm_down_book_missing": down_missing,
            "missing__pm_down_book_stale": down_stale,
        },
        target=target,
        feature_hash=f"feature-{condition_id}",
        input_fingerprint=f"input-{condition_id}",
    )


def _config(module, *, min_validation_trades: int = 3, max_spread=None):
    return module.EdgeConfig(
        fee_rate=0.07,
        slippage_buffer=0.01,
        min_edge_grid=(0.0, 0.01, 0.02, 0.03, 0.05, 0.075, 0.10, 0.15),
        min_validation_trades=min_validation_trades,
        max_spread=max_spread,
    )


def test_up_edge_uses_fresh_up_ask_and_fee_curve() -> None:
    module = _module()
    decision = module.edge_decision(_row(), 0.72, _config(module), 0.02)

    assert decision.side == "up"
    assert decision.ask == 0.60
    assert decision.bid == 0.58
    assert decision.spread == pytest.approx(0.02)
    assert decision.fee == pytest.approx(0.07 * 0.60 * 0.40)
    assert decision.raw_edge == pytest.approx(0.12)
    assert decision.cost_adjusted_edge == pytest.approx(
        0.72 - 0.60 - decision.fee - 0.01
    )
    assert decision.trade is True


def test_down_edge_uses_down_ask() -> None:
    module = _module()
    decision = module.edge_decision(
        _row(target=0, down_ask=0.38, down_bid=0.36),
        0.30,
        _config(module),
        0.02,
    )

    assert decision.side == "down"
    assert decision.side_probability == pytest.approx(0.70)
    assert decision.ask == 0.38
    assert decision.spread == pytest.approx(0.02)
    assert decision.trade is True


def test_stale_selected_side_is_no_fill() -> None:
    module = _module()
    decision = module.edge_decision(
        _row(up_stale=1.0),
        0.72,
        _config(module),
        0.0,
    )

    assert decision.trade is False
    assert decision.executable is False
    assert decision.reason == "selected_book_stale"
    assert decision.ask is None


def test_missing_market_probability_is_not_trade_eligible() -> None:
    module = _module()
    decision = module.edge_decision(
        _row(pm_up_price=None),
        0.72,
        _config(module),
        0.0,
    )

    assert decision.trade is False
    assert decision.executable is False
    assert decision.reason == "missing_market_probability"


def test_spread_gate_abstains_without_double_subtracting_spread() -> None:
    module = _module()
    row = _row(up_ask=0.60, up_bid=0.50)
    open_decision = module.edge_decision(row, 0.72, _config(module), 0.0)
    gated = module.edge_decision(row, 0.72, _config(module, max_spread=0.05), 0.0)

    assert open_decision.cost_adjusted_edge == pytest.approx(
        0.72 - 0.60 - (0.07 * 0.60 * 0.40) - 0.01
    )
    assert gated.trade is False
    assert gated.reason == "spread_too_wide"


def test_no_trade_selected_when_every_validation_threshold_loses() -> None:
    module = _module()
    rows = tuple(
        _row(condition_id=f"c{index}", target=0, up_ask=0.55, up_bid=0.54)
        for index in range(3)
    )
    selection = module.select_validation_edge_policy(
        rows,
        (0.70, 0.72, 0.74),
        _config(module),
    )

    assert selection.policy == "no_trade"
    assert selection.min_edge is None
    assert selection.validation_metrics.trade_count == 0


def test_minimum_validation_trade_gate_blocks_one_lucky_trade() -> None:
    module = _module()
    rows = (
        _row(condition_id="winner", target=1, up_ask=0.55, up_bid=0.54),
        _row(condition_id="missing-1", target=1, up_missing=1.0),
        _row(condition_id="missing-2", target=1, up_missing=1.0),
    )
    selection = module.select_validation_edge_policy(
        rows,
        (0.90, 0.90, 0.90),
        _config(module, min_validation_trades=2),
    )

    assert selection.policy == "no_trade"
    assert selection.min_edge is None


def test_row_wrapper_matches_target_free_predictor_mapping() -> None:
    module = _module()
    row = _row(target=0, down_ask=0.38, down_bid=0.36)
    config = _config(module, max_spread=0.05)

    expected = module.edge_decision(row, 0.30, config, 0.02)
    actual = module.edge_decision_from_predictors(
        row.predictors,
        0.30,
        config,
        0.02,
    )

    assert actual == expected
