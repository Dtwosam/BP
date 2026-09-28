from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bp_engine.execution.fast_live_prepare import (
    build_fast_live_draft,
    frozen_v3_paper_config,
)
from bp_engine.execution.paper import PaperOrderDraft, build_paper_order
from bp_engine.features.hashing import canonical_hash

BASE = datetime(2026, 9, 28, 20, 0, tzinfo=UTC)


def _prediction() -> dict[str, object]:
    return {
        "prediction_id": "prediction-fast-parity",
        "semantic_sha256": "a" * 64,
        "condition_id": "condition-fast-parity",
        "recorded_at": BASE,
        "market_end_at": BASE + timedelta(seconds=60),
        "up_token_id": "up-token-fast",
        "down_token_id": "down-token-fast",
        "selected_side": "up",
        "trade": True,
        "executable": True,
        "selected_ask": Decimal("0.58"),
        "slippage_buffer": Decimal("0.01"),
        "edge_config": {"fee_rate": Decimal("0.07")},
    }


def test_fast_live_draft_is_exact_frozen_paper_formula() -> None:
    prediction = _prediction()
    config = frozen_v3_paper_config()
    cash = Decimal("100")

    ordinary = build_paper_order(prediction, config, cash)
    assert isinstance(ordinary, PaperOrderDraft)

    paper_order_id, fast = build_fast_live_draft(
        prediction,
        available_paper_cash=cash,
    )

    assert fast.request == ordinary.request
    assert fast.signal_selected_ask == ordinary.signal_selected_ask
    assert fast.signal_fee_rate == ordinary.signal_fee_rate
    assert fast.signal_slippage_buffer == ordinary.signal_slippage_buffer
    assert fast.execution_config == ordinary.execution_config
    assert fast.request.limit_price == Decimal("0.59")
    assert fast.request.submitted_at == BASE
    assert fast.request.arrival_at == BASE + timedelta(milliseconds=250)
    assert fast.request.expires_at == BASE + timedelta(milliseconds=2250)
    assert paper_order_id == canonical_hash(
        {
            "prediction_id": fast.request.prediction_id,
            "execution_version": fast.request.execution_version,
        }
    )


def test_fast_live_frozen_config_remains_exact() -> None:
    config = frozen_v3_paper_config()
    assert config.starting_cash_usd == Decimal("100.00")
    assert config.target_notional_usd == Decimal("5.00")
    assert config.latency_ms == 250
    assert config.order_ttl_ms == 2000
    assert config.share_precision == 6
    assert config.execution_version == "paper-execution-v3-frozen-v1"
    assert config.prediction_version == "v3-frozen-paper-v1"
