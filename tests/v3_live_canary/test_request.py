from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bp_engine.execution.models import V3_LIVE_CANARY_EXECUTION_VERSION
from bp_engine.v3_live_canary.request import build_v3_live_canary_request


def _prediction() -> dict[str, object]:
    recorded = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
    return {
        "prediction_id": "prediction-1",
        "semantic_sha256": "a" * 64,
        "prediction_version": "v3-frozen-paper-v1",
        "source_feature_version": "core-v3-btc-native",
        "condition_id": "condition-1",
        "up_token_id": "up-token",
        "down_token_id": "down-token",
        "selected_side": "up",
        "trade": True,
        "executable": True,
        "selected_ask": Decimal("0.50"),
        "selected_offset_seconds": 240,
        "decision_min_edge": Decimal("0.075"),
        "slippage_buffer": Decimal("0.01"),
        "edge_config": {
            "fee_rate": 0.07,
            "slippage_buffer": 0.01,
            "min_edge": 0.075,
            "max_selected_book_age_seconds": 10,
        },
        "recorded_at": recorded,
        "market_end_at": recorded + timedelta(seconds=60),
    }


def test_canary_request_reuses_frozen_v3_order_math() -> None:
    request = build_v3_live_canary_request(_prediction())
    assert request.execution_version == V3_LIVE_CANARY_EXECUTION_VERSION
    assert request.target_notional_usd == Decimal("5.00")
    assert request.limit_price == Decimal("0.51")
    assert request.submitted_at == datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
    assert request.arrival_at == request.submitted_at + timedelta(milliseconds=250)
    assert request.expires_at == request.arrival_at + timedelta(milliseconds=2000)
    worst_fee = Decimal("0.07") * request.limit_price * (
        Decimal("1") - request.limit_price
    )
    assert request.requested_shares * (request.limit_price + worst_fee) <= Decimal(
        "5.00"
    )
    assert request.requested_shares >= Decimal("5")
