from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import create_engine

from bp_engine.execution import canary
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
from bp_engine.execution.live import InterlockDecision
from bp_engine.live_readiness.repository import LiveReadinessRepository
from bp_engine.storage import schema


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

def _ledger_with_first_canary_attempt():
    engine = create_engine("sqlite://")
    schema.metadata.create_all(engine)
    observed = datetime(2026, 9, 26, 14, 0, tzinfo=UTC)
    intent_id = "live-intent-first-canary"
    with engine.begin() as connection:
        connection.execute(
            schema.live_order_intents.insert().values(
                intent_id=intent_id,
                prediction_id="p" * 64,
                policy_version=CANARY_POLICY_VERSION,
                request_id="live-request-first",
                risk_decision_id="live-risk-first",
                token_id="token-down",
                side="BUY",
                size=Decimal("6.8"),
                limit_price=Decimal("0.72"),
                pre_submit_at=observed,
                evidence={"phase": "test"},
                semantic_sha256="a" * 64,
                created_at=observed,
            )
        )
        LiveReadinessRepository().store_order_event(
            connection,
            event_key=f"{intent_id}:accepted",
            intent_id=intent_id,
            event_type="accepted",
            observed_at=observed,
            external_order_id="order-first",
            external_trade_id=None,
            evidence={"phase": "test"},
        )
    return engine, observed


def test_default_prepare_still_stops_after_first_lifetime_attempt() -> None:
    engine, observed = _ledger_with_first_canary_attempt()
    result = canary.prepare_next_canary(
        engine=engine,
        activated_at=observed,
        observed_at=observed,
        interlock=InterlockDecision(eligible=True, reasons=()),
        api_healthy=True,
        official_open_order_count=0,
        collateral_balance_usd=Decimal("30"),
    )

    assert result["status"] == "stopped"
    assert result["reason"] == "canary_submission_attempt_limit_reached"
    assert result["submission_attempt_count"] == 1
    assert result["authorized_submission_attempt_limit"] == 1


def test_explicit_second_canary_ledger_ceiling_allows_preparation_to_continue() -> None:
    engine, observed = _ledger_with_first_canary_attempt()
    result = canary.prepare_next_canary(
        engine=engine,
        activated_at=observed,
        observed_at=observed,
        interlock=InterlockDecision(eligible=True, reasons=()),
        api_healthy=True,
        official_open_order_count=0,
        collateral_balance_usd=Decimal("30"),
        authorized_submission_attempt_limit=2,
        authorized_accepted_order_limit=2,
    )

    assert result == {"status": "waiting", "reason": "no_new_frozen_v3_trade_order"}

