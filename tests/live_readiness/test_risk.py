from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from bp_engine.live_readiness.risk import evaluate_live_risk

from bp_engine.live_readiness.models import LiveAccountSnapshot, LiveRiskContext, LiveRiskPolicy

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
SHA = "a" * 64


def _policy(**overrides: object) -> LiveRiskPolicy:
    values: dict[str, object] = {
        "max_trade_size_usd": Decimal("5"),
        "max_total_exposure_usd": Decimal("20"),
        "max_daily_loss_usd": Decimal("10"),
        "max_consecutive_losses": 3,
        "min_edge": Decimal("0.03"),
        "min_probability": Decimal("0.55"),
        "min_liquidity_usd": Decimal("10"),
        "max_spread": Decimal("0.05"),
        "max_prediction_age_seconds": Decimal("60"),
        "min_time_to_expiry_seconds": Decimal("30"),
        "cooldown_seconds": Decimal("10"),
    }
    values.update(overrides)
    return LiveRiskPolicy(**values)  # type: ignore[arg-type]


def _account(**overrides: object) -> LiveAccountSnapshot:
    values: dict[str, object] = {
        "total_exposure_usd": Decimal("0"),
        "realized_daily_pnl_usd": Decimal("0"),
        "consecutive_losses": 0,
        "last_order_at": None,
        "unresolved_critical_reconciliation": 0,
    }
    values.update(overrides)
    return LiveAccountSnapshot(**values)  # type: ignore[arg-type]


def _context(*, account: LiveAccountSnapshot | None = None, **overrides: object) -> LiveRiskContext:
    values: dict[str, object] = {
        "prediction_id": "prediction-1",
        "prediction_semantic_sha256": SHA,
        "recorded_at": NOW - timedelta(seconds=5),
        "market_end_at": NOW + timedelta(minutes=5),
        "trade": True,
        "executable": True,
        "probability": Decimal("0.70"),
        "expected_edge": Decimal("0.08"),
        "selected_ask": Decimal("0.45"),
        "spread": Decimal("0.02"),
        "selected_liquidity_usd": Decimal("100"),
        "requested_notional_usd": Decimal("5"),
        "observed_at": NOW,
        "api_healthy": True,
        "duplicate_intent": False,
        "account": account or _account(),
    }
    values.update(overrides)
    return LiveRiskContext(**values)  # type: ignore[arg-type]


def _decision(
    *,
    policy: LiveRiskPolicy | None = None,
    context: LiveRiskContext | None = None,
    interlock_eligible: bool = True,
    interlock_reasons: tuple[str, ...] = (),
):
    return evaluate_live_risk(
        policy=policy or _policy(),
        context=context or _context(),
        interlock_eligible=interlock_eligible,
        interlock_reasons=interlock_reasons,
    )


def test_fully_eligible_context_passes_all_rules() -> None:
    decision = _decision()
    assert decision.eligible is True
    assert decision.reasons == ()
    assert all(rule.passed for rule in decision.rules)
    assert len(decision.policy_sha256) == 64
    assert len(decision.semantic_sha256) == 64


def test_live_interlock_blocked_preserves_specific_interlock_reasons() -> None:
    decision = _decision(
        interlock_eligible=False,
        interlock_reasons=("geographic_eligibility_blocked", "activation_manifest_missing"),
    )
    assert decision.eligible is False
    assert decision.reasons[:3] == (
        "live_interlock_blocked",
        "geographic_eligibility_blocked",
        "activation_manifest_missing",
    )


def test_trade_signal_false_blocks() -> None:
    assert "trade_signal_false" in _decision(context=_context(trade=False)).reasons


def test_prediction_not_executable_blocks() -> None:
    assert "prediction_not_executable" in _decision(context=_context(executable=False)).reasons


def test_trade_size_limit_exceeded_blocks() -> None:
    decision = _decision(context=_context(requested_notional_usd=Decimal("5.01")))
    assert "trade_size_limit_exceeded" in decision.reasons


def test_total_exposure_limit_exceeded_blocks() -> None:
    account = _account(total_exposure_usd=Decimal("16"))
    assert "total_exposure_limit_exceeded" in _decision(context=_context(account=account)).reasons


def test_daily_loss_limit_reached_blocks_at_exact_boundary() -> None:
    account = _account(realized_daily_pnl_usd=Decimal("-10"))
    assert "daily_loss_limit_reached" in _decision(context=_context(account=account)).reasons


def test_consecutive_loss_limit_reached_blocks_at_exact_boundary() -> None:
    account = _account(consecutive_losses=3)
    assert "consecutive_loss_limit_reached" in _decision(context=_context(account=account)).reasons


def test_probability_below_minimum_blocks() -> None:
    decision = _decision(context=_context(probability=Decimal("0.54")))
    assert "probability_below_minimum" in decision.reasons


def test_edge_below_minimum_blocks() -> None:
    decision = _decision(context=_context(expected_edge=Decimal("0.029")))
    assert "edge_below_minimum" in decision.reasons


def test_selected_ask_missing_blocks() -> None:
    assert "selected_ask_missing" in _decision(context=_context(selected_ask=None)).reasons


def test_spread_missing_blocks() -> None:
    assert "spread_missing" in _decision(context=_context(spread=None)).reasons


def test_spread_too_wide_blocks() -> None:
    assert "spread_too_wide" in _decision(context=_context(spread=Decimal("0.051"))).reasons


def test_liquidity_missing_blocks() -> None:
    assert "liquidity_missing" in _decision(context=_context(selected_liquidity_usd=None)).reasons


def test_liquidity_below_minimum_blocks() -> None:
    assert "liquidity_below_minimum" in _decision(
        context=_context(selected_liquidity_usd=Decimal("9.99"))
    ).reasons


def test_prediction_stale_blocks() -> None:
    assert "prediction_stale" in _decision(
        context=_context(recorded_at=NOW - timedelta(seconds=61))
    ).reasons


def test_too_close_to_expiry_blocks() -> None:
    assert "too_close_to_expiry" in _decision(
        context=_context(market_end_at=NOW + timedelta(seconds=29))
    ).reasons


def test_cooldown_active_blocks() -> None:
    account = _account(last_order_at=NOW - timedelta(seconds=9))
    assert "cooldown_active" in _decision(context=_context(account=account)).reasons


def test_api_unhealthy_blocks() -> None:
    assert "api_unhealthy" in _decision(context=_context(api_healthy=False)).reasons


def test_duplicate_intent_blocks() -> None:
    assert "duplicate_intent" in _decision(context=_context(duplicate_intent=True)).reasons


def test_reconciliation_blocked() -> None:
    account = _account(unresolved_critical_reconciliation=1)
    assert "reconciliation_blocked" in _decision(context=_context(account=account)).reasons


def test_zero_fail_closed_limits_block_new_exposure() -> None:
    policy = _policy(
        max_trade_size_usd=0,
        max_total_exposure_usd=0,
        max_daily_loss_usd=0,
        max_consecutive_losses=0,
    )
    decision = _decision(policy=policy)
    assert "trade_size_limit_exceeded" in decision.reasons
    assert "total_exposure_limit_exceeded" in decision.reasons
    assert "daily_loss_limit_reached" in decision.reasons
    assert "consecutive_loss_limit_reached" in decision.reasons


def test_all_rules_are_evaluated_without_short_circuiting_and_order_is_deterministic() -> None:
    account = _account(
        total_exposure_usd=Decimal("20"),
        realized_daily_pnl_usd=Decimal("-10"),
        consecutive_losses=3,
        last_order_at=NOW - timedelta(seconds=1),
        unresolved_critical_reconciliation=2,
    )
    context = _context(
        account=account,
        trade=False,
        executable=False,
        probability=Decimal("0.40"),
        expected_edge=Decimal("0.01"),
        selected_ask=None,
        spread=None,
        selected_liquidity_usd=None,
        recorded_at=NOW - timedelta(seconds=120),
        market_end_at=NOW + timedelta(seconds=5),
        api_healthy=False,
        duplicate_intent=True,
    )
    first = _decision(
        context=context,
        interlock_eligible=False,
        interlock_reasons=("geographic_eligibility_blocked",),
    )
    second = _decision(
        context=context,
        interlock_eligible=False,
        interlock_reasons=("geographic_eligibility_blocked",),
    )
    assert len(first.rules) == 20
    assert tuple(rule.rule for rule in first.rules) == (
        "live_interlock",
        "trade_signal",
        "prediction_executable",
        "trade_size_limit",
        "total_exposure_limit",
        "daily_loss_limit",
        "consecutive_loss_limit",
        "probability_minimum",
        "edge_minimum",
        "selected_ask_present",
        "spread_present",
        "spread_limit",
        "liquidity_present",
        "liquidity_minimum",
        "prediction_freshness",
        "time_to_expiry",
        "cooldown",
        "api_health",
        "duplicate_intent",
        "reconciliation",
    )
    assert first.reasons == second.reasons
    assert first.semantic_sha256 == second.semantic_sha256
    assert len(first.reasons) > 10


def test_interlock_reasons_must_be_machine_readable_nonblank_strings() -> None:
    with pytest.raises(ValueError, match="interlock_reasons"):
        _decision(interlock_eligible=False, interlock_reasons=("",))
