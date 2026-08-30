from __future__ import annotations

from decimal import Decimal

from bp_engine.live_readiness.hashing import semantic_sha256
from bp_engine.live_readiness.models import (
    LiveRiskContext,
    LiveRiskDecision,
    LiveRiskPolicy,
    RuleResult,
)


def _seconds(value) -> Decimal:
    return Decimal(str(value.total_seconds()))


def _rule(*, name: str, passed: bool, failure_reason: str) -> RuleResult:
    return RuleResult(rule=name, passed=passed, reason="passed" if passed else failure_reason)


def evaluate_live_risk(
    *,
    policy: LiveRiskPolicy,
    context: LiveRiskContext,
    interlock_eligible: bool,
    interlock_reasons: tuple[str, ...] = (),
) -> LiveRiskDecision:
    """Evaluate every live-risk rule without short-circuiting."""
    normalized_interlock_reasons: list[str] = []
    for reason in interlock_reasons:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("interlock_reasons must contain nonblank strings")
        normalized = reason.strip()
        if normalized not in normalized_interlock_reasons:
            normalized_interlock_reasons.append(normalized)

    prediction_age = _seconds(context.observed_at - context.recorded_at)
    time_to_expiry = _seconds(context.market_end_at - context.observed_at)
    last_order_elapsed = (
        None
        if context.account.last_order_at is None
        else _seconds(context.observed_at - context.account.last_order_at)
    )

    trade_size_ok = (
        policy.max_trade_size_usd > 0
        and context.requested_notional_usd <= policy.max_trade_size_usd
    )
    total_exposure_ok = (
        policy.max_total_exposure_usd > 0
        and context.account.total_exposure_usd + context.requested_notional_usd
        <= policy.max_total_exposure_usd
    )
    daily_loss_ok = (
        policy.max_daily_loss_usd > 0
        and context.account.realized_daily_pnl_usd > -policy.max_daily_loss_usd
    )
    consecutive_loss_ok = (
        policy.max_consecutive_losses > 0
        and context.account.consecutive_losses < policy.max_consecutive_losses
    )
    prediction_fresh = 0 <= prediction_age <= policy.max_prediction_age_seconds
    expiry_ok = time_to_expiry >= policy.min_time_to_expiry_seconds
    cooldown_ok = (
        last_order_elapsed is None
        or (last_order_elapsed >= 0 and last_order_elapsed >= policy.cooldown_seconds)
    )

    rules = (
        _rule(
            name="live_interlock",
            passed=interlock_eligible,
            failure_reason="live_interlock_blocked",
        ),
        _rule(name="trade_signal", passed=context.trade, failure_reason="trade_signal_false"),
        _rule(
            name="prediction_executable",
            passed=context.executable,
            failure_reason="prediction_not_executable",
        ),
        _rule(
            name="trade_size_limit",
            passed=trade_size_ok,
            failure_reason="trade_size_limit_exceeded",
        ),
        _rule(
            name="total_exposure_limit",
            passed=total_exposure_ok,
            failure_reason="total_exposure_limit_exceeded",
        ),
        _rule(
            name="daily_loss_limit",
            passed=daily_loss_ok,
            failure_reason="daily_loss_limit_reached",
        ),
        _rule(
            name="consecutive_loss_limit",
            passed=consecutive_loss_ok,
            failure_reason="consecutive_loss_limit_reached",
        ),
        _rule(
            name="probability_minimum",
            passed=context.probability >= policy.min_probability,
            failure_reason="probability_below_minimum",
        ),
        _rule(
            name="edge_minimum",
            passed=context.expected_edge >= policy.min_edge,
            failure_reason="edge_below_minimum",
        ),
        _rule(
            name="selected_ask_present",
            passed=context.selected_ask is not None,
            failure_reason="selected_ask_missing",
        ),
        _rule(
            name="spread_present",
            passed=context.spread is not None,
            failure_reason="spread_missing",
        ),
        _rule(
            name="spread_limit",
            passed=context.spread is None or context.spread <= policy.max_spread,
            failure_reason="spread_too_wide",
        ),
        _rule(
            name="liquidity_present",
            passed=context.selected_liquidity_usd is not None,
            failure_reason="liquidity_missing",
        ),
        _rule(
            name="liquidity_minimum",
            passed=(
                context.selected_liquidity_usd is None
                or context.selected_liquidity_usd >= policy.min_liquidity_usd
            ),
            failure_reason="liquidity_below_minimum",
        ),
        _rule(
            name="prediction_freshness",
            passed=prediction_fresh,
            failure_reason="prediction_stale",
        ),
        _rule(
            name="time_to_expiry",
            passed=expiry_ok,
            failure_reason="too_close_to_expiry",
        ),
        _rule(name="cooldown", passed=cooldown_ok, failure_reason="cooldown_active"),
        _rule(name="api_health", passed=context.api_healthy, failure_reason="api_unhealthy"),
        _rule(
            name="duplicate_intent",
            passed=not context.duplicate_intent,
            failure_reason="duplicate_intent",
        ),
        _rule(
            name="reconciliation",
            passed=context.account.unresolved_critical_reconciliation == 0,
            failure_reason="reconciliation_blocked",
        ),
    )

    reasons: list[str] = []
    for index, rule in enumerate(rules):
        if rule.passed:
            continue
        if rule.reason not in reasons:
            reasons.append(rule.reason)
        if index == 0:
            for reason in normalized_interlock_reasons:
                if reason not in reasons:
                    reasons.append(reason)

    reasons_tuple = tuple(reasons)
    policy_sha256 = semantic_sha256(policy)
    eligible = not reasons_tuple
    decision_payload = {
        "eligible": eligible,
        "reasons": reasons_tuple,
        "rules": rules,
        "policy_sha256": policy_sha256,
        "context": context,
        "interlock_eligible": interlock_eligible,
        "interlock_reasons": tuple(normalized_interlock_reasons),
    }
    return LiveRiskDecision(
        eligible=eligible,
        reasons=reasons_tuple,
        rules=rules,
        policy_sha256=policy_sha256,
        semantic_sha256=semantic_sha256(decision_payload),
    )
