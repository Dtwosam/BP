from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from bp_engine.v4_paper.fresh_book_shadow import (
    TARGET_NOTIONAL_USD,
    V4FreshBookShadowError,
    evaluate_v4_fresh_book_shadow,
    settle_v4_fresh_book_shadow,
)
from bp_engine.v4_paper.inference import FROZEN_V4_MODEL_SHA256
from bp_engine.v4_paper.source_time_features import V4_SOURCE_TIME_FEATURE_VERSION


def _prediction(probability_up: float = 0.8) -> dict[str, object]:
    decision = datetime(2026, 10, 2, 12, 4, tzinfo=UTC)
    return {
        "prediction_id": "prediction-v4",
        "model_sha256": FROZEN_V4_MODEL_SHA256,
        "source_feature_version": V4_SOURCE_TIME_FEATURE_VERSION,
        "condition_id": "condition-v4",
        "decision_at": decision,
        "recorded_at": decision,
        "probability_up": probability_up,
        "up_token_id": "up-token",
        "down_token_id": "down-token",
    }


def test_v4_fresh_book_shadow_uses_frozen_edge_and_five_dollar_notional() -> None:
    result = evaluate_v4_fresh_book_shadow(
        _prediction(0.8),
        (("0.40", "100"),),
        quote_observed_at=datetime(2026, 10, 2, 12, 4, 0, 100000, tzinfo=UTC),
    )
    assert result.trade is True
    assert result.selected_side == "up"
    assert result.best_ask == Decimal("0.40")
    assert result.requested_shares > 0
    assert result.total_fill_cost <= TARGET_NOTIONAL_USD
    assert result.full_fill is True


def test_v4_fresh_book_shadow_settlement_is_paper_only_math() -> None:
    result = evaluate_v4_fresh_book_shadow(
        _prediction(0.8),
        (("0.40", "100"),),
        quote_observed_at=datetime(2026, 10, 2, 12, 4, 0, 100000, tzinfo=UTC),
    )
    win = settle_v4_fresh_book_shadow(result, official_outcome="up")
    loss = settle_v4_fresh_book_shadow(result, official_outcome="down")
    assert win == result.filled_shares - result.total_fill_cost
    assert loss == -result.total_fill_cost


def test_v4_fresh_book_shadow_rejects_wrong_model() -> None:
    prediction = _prediction()
    prediction["model_sha256"] = "0" * 64
    with pytest.raises(V4FreshBookShadowError, match="model artifact"):
        evaluate_v4_fresh_book_shadow(
            prediction,
            (("0.40", "100"),),
            quote_observed_at=datetime(2026, 10, 2, 12, 4, tzinfo=UTC),
        )
