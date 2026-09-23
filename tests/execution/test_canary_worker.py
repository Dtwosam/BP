from __future__ import annotations

from decimal import Decimal

from bp_engine.execution.canary_worker import (
    CANARY_MAX_CONSECUTIVE_LOSSES,
    CANARY_MAX_DAILY_LOSS_USD,
    CANARY_MAX_TOTAL_EXPOSURE_USD,
    CANARY_MIN_EDGE,
    CANARY_POLICY_VERSION,
    CANARY_TARGET_NOTIONAL_USD,
    _activation_sha,
    _policy,
)
from bp_engine.execution.models import V3_LIVE_CANARY_EXECUTION_VERSION


def test_ten_dollar_canary_policy_is_tiny_and_frozen() -> None:
    policy = _policy()
    assert CANARY_TARGET_NOTIONAL_USD == Decimal("10.00")
    assert CANARY_MAX_TOTAL_EXPOSURE_USD == Decimal("10.00")
    assert CANARY_MAX_DAILY_LOSS_USD == Decimal("10.00")
    assert CANARY_MAX_CONSECUTIVE_LOSSES == 1
    assert CANARY_MIN_EDGE == Decimal("0.075")
    assert policy.max_trade_size_usd == Decimal("10.00")
    assert policy.max_total_exposure_usd == Decimal("10.00")
    assert policy.max_daily_loss_usd == Decimal("10.00")
    assert policy.max_consecutive_losses == 1
    assert policy.min_edge == Decimal("0.075")
    assert policy.policy_version == CANARY_POLICY_VERSION
    assert V3_LIVE_CANARY_EXECUTION_VERSION == "live-execution-v3-canary-10usd-v1"


def test_activation_digest_binds_exact_git_sha() -> None:
    first = _activation_sha("a" * 40)
    second = _activation_sha("b" * 40)
    assert len(first) == 64
    assert first != second
