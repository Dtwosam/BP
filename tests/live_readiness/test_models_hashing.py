from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from bp_engine.live_readiness.hashing import semantic_sha256
from bp_engine.live_readiness.models import (
    LIVE_POLICY_VERSION,
    ActivationManifest,
    GeoblockResult,
    LiveAccountSnapshot,
    LiveRiskContext,
    LiveRiskDecision,
    LiveRiskPolicy,
    RuleResult,
)

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
        "max_prediction_age_seconds": Decimal("10"),
        "min_time_to_expiry_seconds": Decimal("30"),
        "cooldown_seconds": Decimal("5"),
    }
    values.update(overrides)
    return LiveRiskPolicy(**values)  # type: ignore[arg-type]


def test_policy_is_immutable_and_normalizes_decimals() -> None:
    policy = _policy(max_trade_size_usd=5)
    assert policy.policy_version == LIVE_POLICY_VERSION
    assert policy.max_trade_size_usd == Decimal("5")
    with pytest.raises(FrozenInstanceError):
        policy.max_trade_size_usd = Decimal("6")  # type: ignore[misc]


def test_policy_rejects_negative_or_inconsistent_limits() -> None:
    with pytest.raises(ValueError, match="max_trade_size_usd"):
        _policy(max_trade_size_usd=Decimal("-1"))
    with pytest.raises(ValueError, match="max_trade_size_usd"):
        _policy(max_trade_size_usd=Decimal("25"), max_total_exposure_usd=Decimal("20"))


def test_models_require_timezone_aware_timestamps_and_valid_sha() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        GeoblockResult(blocked=False, country="NL", region="NH", checked_at=datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="SHA-256"):
        LiveRiskContext(
            prediction_id="p1",
            prediction_semantic_sha256="bad",
            recorded_at=NOW,
            market_end_at=NOW + timedelta(minutes=5),
            trade=True,
            executable=True,
            probability=Decimal("0.7"),
            expected_edge=Decimal("0.1"),
            selected_ask=Decimal("0.5"),
            spread=Decimal("0.02"),
            selected_liquidity_usd=Decimal("50"),
            requested_notional_usd=Decimal("5"),
            observed_at=NOW,
            api_healthy=True,
            duplicate_intent=False,
            account=LiveAccountSnapshot(
                total_exposure_usd=Decimal("0"),
                realized_daily_pnl_usd=Decimal("0"),
                consecutive_losses=0,
                last_order_at=None,
                unresolved_critical_reconciliation=0,
            ),
        )


def test_probability_and_selected_ask_ranges_are_validated() -> None:
    account = LiveAccountSnapshot(
        total_exposure_usd=Decimal("0"),
        realized_daily_pnl_usd=Decimal("0"),
        consecutive_losses=0,
        last_order_at=None,
        unresolved_critical_reconciliation=0,
    )
    with pytest.raises(ValueError, match="probability"):
        LiveRiskContext(
            prediction_id="p1",
            prediction_semantic_sha256=SHA,
            recorded_at=NOW,
            market_end_at=NOW + timedelta(minutes=5),
            trade=True,
            executable=True,
            probability=Decimal("1.1"),
            expected_edge=Decimal("0.1"),
            selected_ask=Decimal("0.5"),
            spread=Decimal("0.02"),
            selected_liquidity_usd=Decimal("50"),
            requested_notional_usd=Decimal("5"),
            observed_at=NOW,
            api_healthy=True,
            duplicate_intent=False,
            account=account,
        )
    with pytest.raises(ValueError, match="selected_ask"):
        LiveRiskContext(
            prediction_id="p1",
            prediction_semantic_sha256=SHA,
            recorded_at=NOW,
            market_end_at=NOW + timedelta(minutes=5),
            trade=True,
            executable=True,
            probability=Decimal("0.7"),
            expected_edge=Decimal("0.1"),
            selected_ask=Decimal("0"),
            spread=Decimal("0.02"),
            selected_liquidity_usd=Decimal("50"),
            requested_notional_usd=Decimal("5"),
            observed_at=NOW,
            api_healthy=True,
            duplicate_intent=False,
            account=account,
        )


def test_activation_manifest_validates_sha_and_time_order() -> None:
    manifest = ActivationManifest(
        authorized=True,
        git_sha=SHA,
        authorization_id="auth-123",
        issued_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )
    assert manifest.git_sha == SHA
    with pytest.raises(ValueError, match="expires_at"):
        ActivationManifest(
            authorized=True,
            git_sha=SHA,
            authorization_id="auth-123",
            issued_at=NOW,
            expires_at=NOW,
        )


def test_semantic_hash_is_deterministic_for_equivalent_decimal_values() -> None:
    left = _policy(max_trade_size_usd=Decimal("5.0"))
    right = _policy(max_trade_size_usd=Decimal("5.000"))
    assert semantic_sha256(left) == semantic_sha256(right)
    assert len(semantic_sha256(left)) == 64


def test_risk_decision_reasons_and_rules_are_immutable_tuples() -> None:
    rules = (RuleResult(rule="edge", passed=False, reason="edge_below_minimum"),)
    decision = LiveRiskDecision(
        eligible=False,
        reasons=("edge_below_minimum",),
        rules=rules,
        policy_sha256=SHA,
        semantic_sha256="b" * 64,
    )
    assert decision.reasons == ("edge_below_minimum",)
    assert decision.rules == rules
