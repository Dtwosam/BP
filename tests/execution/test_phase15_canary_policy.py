from __future__ import annotations

from decimal import Decimal

from bp_engine.execution.canary import (
    CANARY_MAX_ACCEPTED_ORDERS,
    CANARY_MAX_CONSECUTIVE_LOSSES,
    CANARY_MAX_DAILY_LOSS_USD,
    CANARY_MAX_SUBMISSION_ATTEMPTS,
    CANARY_MAX_TOTAL_EXPOSURE_USD,
    CANARY_MAX_TRADE_SIZE_USD,
    CANARY_MIN_EDGE,
    CANARY_POLICY_VERSION,
    CANARY_TARGET_NOTIONAL_USD,
    canary_policy,
)


def test_canary_policy_matches_explicit_ten_dollar_ceiling() -> None:
    policy = canary_policy()
    assert CANARY_POLICY_VERSION == "v3-live-canary-v1"
    assert CANARY_MAX_TRADE_SIZE_USD == Decimal("10")
    assert CANARY_MAX_TOTAL_EXPOSURE_USD == Decimal("10")
    assert CANARY_MAX_DAILY_LOSS_USD == Decimal("10")
    assert CANARY_MAX_CONSECUTIVE_LOSSES == 1
    assert CANARY_MAX_ACCEPTED_ORDERS == 1
    assert CANARY_MAX_SUBMISSION_ATTEMPTS == 1
    assert CANARY_TARGET_NOTIONAL_USD == Decimal("5")
    assert CANARY_MIN_EDGE == Decimal("0.075")
    assert policy.max_trade_size_usd == Decimal("10")
    assert policy.max_total_exposure_usd == Decimal("10")
    assert policy.max_daily_loss_usd == Decimal("10")
    assert policy.max_consecutive_losses == 1
    assert policy.policy_version == "v3-live-canary-v1"
