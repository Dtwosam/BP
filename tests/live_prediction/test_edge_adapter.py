from __future__ import annotations

import importlib

import pytest


def _module():
    return importlib.import_module("bp_engine.calibration.edge")


def _config(module):
    return module.EdgeConfig(
        fee_rate=0.07,
        slippage_buffer=0.01,
        min_edge_grid=(0.0, 0.02, 0.05),
        min_validation_trades=3,
        max_spread=None,
    )


def _predictors(**overrides):
    predictors = {
        "pm_up_price": 0.70,
        "pm_up_best_ask": 0.60,
        "pm_up_best_bid": 0.58,
        "pm_down_best_ask": 0.42,
        "pm_down_best_bid": 0.40,
        "missing__pm_up_book_missing": 0.0,
        "missing__pm_up_book_stale": 0.0,
        "missing__pm_down_book_missing": 0.0,
        "missing__pm_down_book_stale": 0.0,
    }
    predictors.update(overrides)
    return predictors


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"missing__pm_up_book_missing": 1.0}, "selected_book_missing"),
        ({"missing__pm_up_book_stale": 1.0}, "selected_book_stale"),
        ({"pm_up_best_ask": None}, "selected_ask_unavailable"),
    ],
)
def test_mapping_api_preserves_selected_side_unavailable_semantics(
    overrides,
    reason: str,
) -> None:
    module = _module()

    decision = module.edge_decision_from_predictors(
        _predictors(**overrides),
        0.72,
        _config(module),
        0.0,
    )

    assert decision.executable is False
    assert decision.trade is False
    assert decision.reason == reason
    assert decision.ask is None


def test_mapping_api_uses_selected_down_ask_without_any_target() -> None:
    module = _module()

    decision = module.edge_decision_from_predictors(
        _predictors(pm_down_best_ask=0.38, pm_down_best_bid=0.36),
        0.30,
        _config(module),
        0.02,
    )

    assert decision.side == "down"
    assert decision.predicted_target == 0
    assert decision.ask == 0.38
    assert decision.bid == 0.36
    assert decision.trade is True


def test_mapping_api_missing_market_probability_is_explicit() -> None:
    module = _module()

    decision = module.edge_decision_from_predictors(
        _predictors(pm_up_price=None),
        0.72,
        _config(module),
        0.0,
    )

    assert decision.market_probability_observed is False
    assert decision.executable is False
    assert decision.reason == "missing_market_probability"
