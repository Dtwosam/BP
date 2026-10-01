from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bp_engine.v3_paper.fresh_book_shadow import (
    V3_FRESH_BOOK_SHADOW_VERSION,
    FreshBookShadowError,
    evaluate_fresh_book_shadow,
    settle_fresh_book_shadow,
)
from bp_engine.v3_paper.service import V3_PAPER_PREDICTION_VERSION

NOW = datetime(2026, 10, 1, 20, 0, tzinfo=UTC)


def _prediction(*, probability: str = "0.82") -> dict[str, object]:
    return {
        "prediction_id": "prediction-1",
        "prediction_version": V3_PAPER_PREDICTION_VERSION,
        "condition_id": "condition-1",
        "recorded_at": NOW,
        "up_token_id": "up-token",
        "down_token_id": "down-token",
        "calibrated_probability": Decimal(probability),
    }


def test_fresh_book_shadow_recomputes_trade_from_fresh_selected_ask() -> None:
    result = evaluate_fresh_book_shadow(
        _prediction(probability="0.82"),
        [("0.50", "20"), ("0.51", "20")],
        quote_observed_at=NOW + timedelta(milliseconds=20),
    )

    assert result.shadow_version == V3_FRESH_BOOK_SHADOW_VERSION
    assert result.selected_side == "up"
    assert result.token_id == "up-token"
    assert result.best_ask == Decimal("0.50")
    assert result.trade is True
    assert result.decision_reason == "trade"
    assert result.limit_price == Decimal("0.51")
    assert result.requested_shares > 0
    assert result.filled_shares == result.requested_shares
    assert result.full_fill is True
    assert result.total_fill_cost > 0
    assert len(result.semantic_sha256) == 64


def test_fresh_book_shadow_rejects_edge_that_disappears_at_fresh_ask() -> None:
    result = evaluate_fresh_book_shadow(
        _prediction(probability="0.82"),
        [("0.76", "20")],
        quote_observed_at=NOW + timedelta(milliseconds=10),
    )

    assert result.best_ask == Decimal("0.76")
    assert result.trade is False
    assert result.decision_reason == "edge_below_minimum"
    assert result.requested_shares == 0
    assert result.filled_shares == 0
    assert result.total_fill_cost == 0


def test_fresh_book_shadow_uses_down_token_when_probability_below_half() -> None:
    result = evaluate_fresh_book_shadow(
        _prediction(probability="0.18"),
        [("0.50", "20")],
        quote_observed_at=NOW + timedelta(milliseconds=10),
    )

    assert result.selected_side == "down"
    assert result.token_id == "down-token"
    assert result.side_probability == Decimal("0.82")
    assert result.trade is True


def test_fresh_book_shadow_models_partial_displayed_depth() -> None:
    result = evaluate_fresh_book_shadow(
        _prediction(probability="0.82"),
        [("0.50", "1"), ("0.505", "1"), ("0.70", "100")],
        quote_observed_at=NOW + timedelta(milliseconds=10),
    )

    assert result.trade is True
    assert result.requested_shares > Decimal("2")
    assert result.filled_shares == Decimal("2")
    assert result.full_fill is False
    assert result.gross_fill_cost == Decimal("1.005")


def test_fresh_book_shadow_settlement_uses_only_modeled_filled_shares() -> None:
    result = evaluate_fresh_book_shadow(
        _prediction(probability="0.82"),
        [("0.50", "20")],
        quote_observed_at=NOW + timedelta(milliseconds=10),
    )

    win = settle_fresh_book_shadow(result, official_outcome="Up")
    loss = settle_fresh_book_shadow(result, official_outcome="Down")

    assert win == result.filled_shares - result.total_fill_cost
    assert loss == -result.total_fill_cost


def test_fresh_book_shadow_requires_frozen_prediction_version() -> None:
    prediction = _prediction()
    prediction["prediction_version"] = "other"

    with pytest.raises(FreshBookShadowError, match="unsupported prediction version"):
        evaluate_fresh_book_shadow(
            prediction,
            [("0.50", "20")],
            quote_observed_at=NOW,
        )


def test_fresh_book_shadow_quote_cannot_predate_prediction() -> None:
    with pytest.raises(FreshBookShadowError, match="cannot predate"):
        evaluate_fresh_book_shadow(
            _prediction(),
            [("0.50", "20")],
            quote_observed_at=NOW - timedelta(milliseconds=1),
        )
