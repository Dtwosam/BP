from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select

from bp_engine.execution import canary
from bp_engine.execution.live import _account_snapshot
from bp_engine.live_readiness.repository import LiveReadinessRepository
from bp_engine.storage import schema

BASE = datetime(2026, 9, 23, 16, 50, tzinfo=UTC)
INTENT_ID = "live-intent-test-unsubmitted"


def _engine():
    engine = create_engine("sqlite://")
    schema.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            schema.live_order_intents.insert().values(
                intent_id=INTENT_ID,
                prediction_id="p" * 64,
                policy_version=canary.CANARY_POLICY_VERSION,
                request_id="live-request-test",
                risk_decision_id="live-risk-test",
                token_id="token-up",
                side="BUY",
                size=Decimal("7.8"),
                limit_price=Decimal("0.62"),
                pre_submit_at=BASE,
                evidence={"phase": "phase15_v3_live_canary_v1"},
                semantic_sha256="a" * 64,
                created_at=BASE,
            )
        )
    return engine


def _safe_health() -> dict[str, object]:
    return {
        "status": "ok",
        "geoblock": {"blocked": False, "country": "ZA", "region": "GP"},
        "account": {
            "open_order_count": 0,
            "collateral_balance_usd": "30",
            "clean_for_canary": True,
        },
        "kill_switch_engaged": True,
        "activation_valid": False,
        "submission_ready": False,
        "live_order_submitted": False,
    }


def test_prepare_armability_floor_is_stricter_than_strategy_expiry_floor() -> None:
    assert canary.CANARY_MIN_TIME_TO_EXPIRY_SECONDS == Decimal("15")
    assert canary.CANARY_MIN_PREPARE_ARM_WINDOW_SECONDS == Decimal("30")
    assert (
        canary.CANARY_MIN_PREPARE_ARM_WINDOW_SECONDS
        > canary.CANARY_MIN_TIME_TO_EXPIRY_SECONDS
    )


def test_closed_before_submission_does_not_consume_attempt() -> None:
    engine = _engine()

    result = canary.reconcile_unsubmitted_canary_intent(
        engine=engine,
        intent_id=INTENT_ID,
        observed_at=BASE,
        reason="prepared_market_no_longer_armable",
        executor_health=_safe_health(),
    )

    assert result["status"] == "reconciled"
    assert result["event_type"] == canary.CANARY_PRE_SUBMISSION_CLOSED_EVENT
    assert result["submission_attempt_consumed"] is False

    with engine.connect() as connection:
        events = tuple(
            connection.execute(
                select(schema.live_order_events.c.event_type).where(
                    schema.live_order_events.c.intent_id == INTENT_ID
                )
            ).scalars()
        )
        assert events == (canary.CANARY_PRE_SUBMISSION_CLOSED_EVENT,)
        assert canary._submission_attempt_count(connection) == 0
        assert canary.CANARY_PRE_SUBMISSION_CLOSED_EVENT in canary.CANARY_INTENT_TERMINAL_EVENTS


def test_closed_before_submission_clears_exposure_cooldown_and_reconciliation() -> None:
    engine = _engine()
    canary.reconcile_unsubmitted_canary_intent(
        engine=engine,
        intent_id=INTENT_ID,
        observed_at=BASE,
        reason="prepared_market_no_longer_armable",
        executor_health=_safe_health(),
    )

    with engine.connect() as connection:
        account = _account_snapshot(connection, observed_at=BASE)

    assert account.total_exposure_usd == Decimal("0")
    assert account.last_order_at is None
    assert account.unresolved_critical_reconciliation == 0


def test_pre_submission_reconciliation_cannot_mask_real_attempt() -> None:
    engine = _engine()
    repository = LiveReadinessRepository()
    with engine.begin() as connection:
        repository.store_order_event(
            connection,
            event_key=f"{INTENT_ID}:rejected",
            intent_id=INTENT_ID,
            event_type="rejected",
            observed_at=BASE,
            external_order_id=None,
            external_trade_id=None,
            evidence={"phase": "test"},
        )
        repository.store_reconciliation_run(
            connection,
            observed_at=BASE,
            unresolved_count=0,
            critical_count=0,
            evidence={
                "reconciliation_kind": "pre_submission_intent_close",
                "submission_attempt_consumed": False,
                "official_open_order_count": 0,
            },
        )

    with engine.connect() as connection:
        account = _account_snapshot(connection, observed_at=BASE)

    assert account.total_exposure_usd == Decimal("0")
    assert account.last_order_at == BASE
    assert account.unresolved_critical_reconciliation == 1


def test_reconcile_unsubmitted_is_idempotent() -> None:
    engine = _engine()
    first = canary.reconcile_unsubmitted_canary_intent(
        engine=engine,
        intent_id=INTENT_ID,
        observed_at=BASE,
        reason="prepared_market_no_longer_armable",
        executor_health=_safe_health(),
    )
    second = canary.reconcile_unsubmitted_canary_intent(
        engine=engine,
        intent_id=INTENT_ID,
        observed_at=BASE,
        reason="prepared_market_no_longer_armable",
        executor_health=_safe_health(),
    )

    assert first["status"] == "reconciled"
    assert second["status"] == "already_reconciled"
    with engine.connect() as connection:
        count = connection.execute(
            select(schema.live_order_events.c.id).where(
                schema.live_order_events.c.intent_id == INTENT_ID,
                schema.live_order_events.c.event_type
                == canary.CANARY_PRE_SUBMISSION_CLOSED_EVENT,
            )
        ).all()
        assert len(count) == 1
        assert canary._submission_attempt_count(connection) == 0


def test_reconcile_unsubmitted_refuses_existing_submission_attempt() -> None:
    engine = _engine()
    repository = LiveReadinessRepository()
    with engine.begin() as connection:
        repository.store_order_event(
            connection,
            event_key=f"{INTENT_ID}:rejected",
            intent_id=INTENT_ID,
            event_type="rejected",
            observed_at=BASE,
            external_order_id=None,
            external_trade_id=None,
            evidence={"phase": "test"},
        )

    with pytest.raises(RuntimeError, match="canary_submission_attempt_already_recorded"):
        canary.reconcile_unsubmitted_canary_intent(
            engine=engine,
            intent_id=INTENT_ID,
            observed_at=BASE,
            reason="prepared_market_no_longer_armable",
            executor_health=_safe_health(),
        )

    with engine.connect() as connection:
        assert canary._submission_attempt_count(connection) == 1


@pytest.mark.parametrize(
    ("field", "value", "error"),
    (
        ("kill_switch_engaged", False, "kill_switch_not_engaged"),
        ("activation_valid", True, "activation_still_valid"),
        ("submission_ready", True, "submission_still_ready"),
        ("live_order_submitted", True, "live_order_submission_detected"),
    ),
)
def test_reconcile_unsubmitted_requires_safe_executor_state(
    field: str,
    value: object,
    error: str,
) -> None:
    engine = _engine()
    health = _safe_health()
    health[field] = value

    with pytest.raises(RuntimeError, match=error):
        canary.reconcile_unsubmitted_canary_intent(
            engine=engine,
            intent_id=INTENT_ID,
            observed_at=BASE,
            reason="prepared_market_no_longer_armable",
            executor_health=health,
        )
