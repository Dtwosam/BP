from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine, select

from bp_engine.execution.fast_live_result import (
    fast_live_account_snapshot,
    record_fast_live_official_reconciliation,
    record_fast_live_result,
    settle_fast_live_position_if_resolved,
)
from bp_engine.execution.live import _account_snapshot
from bp_engine.live_readiness.repository import LiveReadinessRepository
from bp_engine.storage import schema

BASE = datetime(2026, 9, 28, 20, 0, tzinfo=UTC)
INTENT_ID = "live-intent-fast-result"
REQUEST_SHA = "1" * 64
PREDICTION_ID = "2" * 64
PAPER_ORDER_ID = "3" * 64


def _engine():
    engine = create_engine("sqlite://")
    schema.metadata.create_all(engine)
    with engine.begin() as connection:
        LiveReadinessRepository().store_reconciliation_run(
            connection,
            observed_at=BASE,
            unresolved_count=0,
            critical_count=0,
            evidence={
                "source": "test-baseline",
                "account_snapshot": {
                    "total_exposure_usd": "0",
                    "realized_daily_pnl_usd": "0",
                    "consecutive_losses": 0,
                },
            },
        )
        connection.execute(
            schema.live_order_intents.insert().values(
                intent_id=INTENT_ID,
                prediction_id=PREDICTION_ID,
                policy_version="v3-live-canary-v1",
                request_id="live-request-fast-result",
                risk_decision_id="risk-fast-result",
                token_id="token-fast-result",
                side="BUY",
                size=Decimal("8.238141"),
                limit_price=Decimal("0.59"),
                pre_submit_at=BASE + timedelta(seconds=1),
                evidence={
                    "phase": "phase15_v3_fast_live_v1",
                    "paper_order_id": PAPER_ORDER_ID,
                    "request_sha256": REQUEST_SHA,
                    "selected_side": "up",
                    "modeled_fee_rate": "0.07",
                },
                semantic_sha256="4" * 64,
                created_at=BASE + timedelta(seconds=1),
            )
        )
    return engine


def _result(
    status: str,
    *,
    attempted: bool,
    order_id: str | None = None,
) -> dict[str, object]:
    return {
        "status": status,
        "intent_id": INTENT_ID,
        "prediction_id": PREDICTION_ID,
        "paper_order_id": PAPER_ORDER_ID,
        "request_sha256": REQUEST_SHA,
        "network_submission_attempt_consumed": attempted,
        "real_order_submitted": attempted,
        "external_order_id": order_id,
        "marketability": {
            "best_ask": "0.58",
            "limit_price": "0.59",
            "marketable": True,
        },
        "quote_source": "stream",
        "quote_latency_ms": 0.2,
        "sign_latency_ms": 1.1,
        "quote_to_post_ms": 0.5,
        "post_latency_ms": 18.0,
    }


def _add_evaluation(engine, official_outcome: str) -> None:
    target = 1 if official_outcome == "Up" else 0
    with engine.begin() as connection:
        connection.execute(
            schema.live_prediction_evaluations.insert().values(
                prediction_id=PREDICTION_ID,
                label_version="official-outcome-v1",
                official_outcome=official_outcome,
                official_target=target,
                label_source="polymarket_gamma_snapshot",
                label_source_snapshot_sha256="5" * 64,
                label_source_observed_at=BASE + timedelta(minutes=5),
                evaluated_at=BASE + timedelta(minutes=5, seconds=1),
                correct=True,
                raw_log_loss=Decimal("0.1"),
                raw_brier=Decimal("0.01"),
                calibrated_log_loss=Decimal("0.1"),
                calibrated_brier=Decimal("0.01"),
                hypothetical_gross_pnl=None,
                hypothetical_assumed_cost_pnl=None,
                semantic_sha256="6" * 64,
            )
        )


def _latest_reconciliation(engine):
    with engine.begin() as connection:
        return connection.execute(
            select(schema.live_reconciliation_runs).order_by(
                schema.live_reconciliation_runs.c.observed_at.desc(),
                schema.live_reconciliation_runs.c.id.desc(),
            )
        ).mappings().first()


def test_fresh_book_rejection_closes_without_consuming_attempt() -> None:
    engine = _engine()
    result = _result("fresh_book_rejected", attempted=False)
    recorded = record_fast_live_result(
        engine=engine,
        result=result,
        observed_at=BASE + timedelta(seconds=2),
    )

    assert recorded["event_type"] == "closed_before_submission"
    assert recorded["official_reconciliation_required"] is False
    latest = _latest_reconciliation(engine)
    assert latest is not None
    assert latest["unresolved_count"] == 0
    assert latest["critical_count"] == 0

    with engine.begin() as connection:
        account = _account_snapshot(
            connection,
            observed_at=BASE + timedelta(seconds=3),
        )
    assert account.total_exposure_usd == 0
    assert account.unresolved_critical_reconciliation == 0


def test_approval_recovery_blocked_closes_without_consuming_attempt() -> None:
    engine = _engine()
    result = _result("approval_recovery_blocked", attempted=False)
    recorded = record_fast_live_result(
        engine=engine,
        result=result,
        observed_at=BASE + timedelta(seconds=2),
    )

    assert recorded["event_type"] == "closed_before_submission"
    assert recorded["official_reconciliation_required"] is False
    latest = _latest_reconciliation(engine)
    assert latest is not None
    assert latest["unresolved_count"] == 0
    assert latest["critical_count"] == 0


def test_accepted_order_blocks_until_official_zero_fill() -> None:
    engine = _engine()
    result = _result("accepted", attempted=True, order_id="order-fast-1")
    result["accepted"] = True
    result["cancellation"] = {
        "cancelled": True,
        "not_cancelled": "",
    }
    recorded = record_fast_live_result(
        engine=engine,
        result=result,
        observed_at=BASE + timedelta(seconds=2),
    )

    assert recorded["event_type"] == "accepted"
    assert recorded["official_reconciliation_required"] is True
    latest = _latest_reconciliation(engine)
    assert latest is not None
    assert latest["critical_count"] == 1

    with engine.begin() as connection:
        before = _account_snapshot(
            connection,
            observed_at=BASE + timedelta(seconds=3),
        )
    assert before.total_exposure_usd > 0
    assert before.unresolved_critical_reconciliation == 1

    official = {
        "order_still_open": False,
        "open_order_count": 0,
        "matching_trade_count": 0,
        "confirmed_filled_shares": "0",
        "confirmed_filled_notional_usd": "0",
        "fill_state": "zero_fill_observed",
    }
    reconciled = record_fast_live_official_reconciliation(
        engine=engine,
        result=result,
        official=official,
        observed_at=BASE + timedelta(seconds=4),
    )
    assert reconciled["settlement_reconciliation_required"] is False

    with engine.begin() as connection:
        after = _account_snapshot(
            connection,
            observed_at=BASE + timedelta(seconds=5),
        )
    assert after.total_exposure_usd == 0
    assert after.unresolved_critical_reconciliation == 0


def test_confirmed_fill_remains_exposure_and_settlement_blocking() -> None:
    engine = _engine()
    result = _result("accepted", attempted=True, order_id="order-fast-fill")
    result["accepted"] = True
    result["cancellation"] = {
        "cancelled": True,
        "not_cancelled": "",
    }
    record_fast_live_result(
        engine=engine,
        result=result,
        observed_at=BASE + timedelta(seconds=2),
    )
    official = {
        "order_still_open": False,
        "open_order_count": 0,
        "matching_trade_count": 1,
        "confirmed_filled_shares": "4",
        "confirmed_filled_notional_usd": "2.32",
        "fill_state": "confirmed_fill",
    }
    reconciled = record_fast_live_official_reconciliation(
        engine=engine,
        result=result,
        official=official,
        observed_at=BASE + timedelta(seconds=4),
    )

    assert reconciled["settlement_reconciliation_required"] is True
    latest = _latest_reconciliation(engine)
    assert latest is not None
    assert latest["unresolved_count"] == 1
    assert latest["critical_count"] == 1

    with engine.begin() as connection:
        account = _account_snapshot(
            connection,
            observed_at=BASE + timedelta(seconds=5),
        )
    assert account.total_exposure_usd >= Decimal("2.32")
    assert account.unresolved_critical_reconciliation == 1


def test_submission_unknown_is_critical_and_never_clean() -> None:
    engine = _engine()
    result = _result("submission_unknown", attempted=True)
    result["real_order_submitted"] = True
    recorded = record_fast_live_result(
        engine=engine,
        result=result,
        observed_at=BASE + timedelta(seconds=2),
    )

    assert recorded["event_type"] == "submission_unknown"
    assert recorded["official_reconciliation_required"] is True
    latest = _latest_reconciliation(engine)
    assert latest is not None
    assert latest["unresolved_count"] == 1
    assert latest["critical_count"] == 1

def test_confirmed_fill_win_settles_and_resets_loss_counter() -> None:
    engine = _engine()
    result = _result("accepted", attempted=True, order_id="order-fast-win")
    result["accepted"] = True
    result["cancellation"] = {"cancelled": True, "not_cancelled": ""}
    record_fast_live_result(
        engine=engine,
        result=result,
        observed_at=BASE + timedelta(seconds=2),
    )
    official = {
        "order_still_open": False,
        "open_order_count": 0,
        "matching_trade_count": 1,
        "confirmed_filled_shares": "4",
        "confirmed_filled_notional_usd": "2.32",
        "fill_state": "confirmed_fill",
    }
    record_fast_live_official_reconciliation(
        engine=engine,
        result=result,
        official=official,
        observed_at=BASE + timedelta(seconds=4),
    )
    _add_evaluation(engine, "Up")

    settled = settle_fast_live_position_if_resolved(
        engine=engine,
        intent_id=INTENT_ID,
        observed_at=BASE + timedelta(minutes=5, seconds=2),
    )

    expected_fee = (
        Decimal("4")
        * Decimal("0.07")
        * Decimal("0.58")
        * Decimal("0.42")
    )
    expected_pnl = Decimal("4") - Decimal("2.32") - expected_fee
    assert settled["status"] == "settled"
    assert Decimal(str(settled["risk_realized_pnl_usd"])) == expected_pnl
    assert settled["consecutive_losses"] == 0

    with engine.begin() as connection:
        account = fast_live_account_snapshot(
            connection,
            observed_at=BASE + timedelta(minutes=5, seconds=3),
        )
    assert account.total_exposure_usd == 0
    assert account.realized_daily_pnl_usd == expected_pnl
    assert account.consecutive_losses == 0
    assert account.unresolved_critical_reconciliation == 0


def test_confirmed_fill_loss_settles_into_one_loss_stop() -> None:
    engine = _engine()
    result = _result("accepted", attempted=True, order_id="order-fast-loss")
    result["accepted"] = True
    result["cancellation"] = {"cancelled": True, "not_cancelled": ""}
    record_fast_live_result(
        engine=engine,
        result=result,
        observed_at=BASE + timedelta(seconds=2),
    )
    official = {
        "order_still_open": False,
        "open_order_count": 0,
        "matching_trade_count": 1,
        "confirmed_filled_shares": "4",
        "confirmed_filled_notional_usd": "2.32",
        "fill_state": "confirmed_fill",
    }
    record_fast_live_official_reconciliation(
        engine=engine,
        result=result,
        official=official,
        observed_at=BASE + timedelta(seconds=4),
    )
    _add_evaluation(engine, "Down")

    settled = settle_fast_live_position_if_resolved(
        engine=engine,
        intent_id=INTENT_ID,
        observed_at=BASE + timedelta(minutes=5, seconds=2),
    )

    expected_fee = (
        Decimal("4")
        * Decimal("0.07")
        * Decimal("0.58")
        * Decimal("0.42")
    )
    expected_pnl = -Decimal("2.32") - expected_fee
    assert settled["status"] == "settled"
    assert Decimal(str(settled["risk_realized_pnl_usd"])) == expected_pnl
    assert settled["consecutive_losses"] == 1

    with engine.begin() as connection:
        account = fast_live_account_snapshot(
            connection,
            observed_at=BASE + timedelta(minutes=5, seconds=3),
        )
    assert account.total_exposure_usd == 0
    assert account.realized_daily_pnl_usd == expected_pnl
    assert account.consecutive_losses == 1
    assert account.unresolved_critical_reconciliation == 0


def test_fast_live_settlement_is_idempotent() -> None:
    engine = _engine()
    result = _result("accepted", attempted=True, order_id="order-fast-idempotent")
    result["accepted"] = True
    result["cancellation"] = {"cancelled": True, "not_cancelled": ""}
    record_fast_live_result(
        engine=engine,
        result=result,
        observed_at=BASE + timedelta(seconds=2),
    )
    official = {
        "order_still_open": False,
        "open_order_count": 0,
        "matching_trade_count": 1,
        "confirmed_filled_shares": "4",
        "confirmed_filled_notional_usd": "2.32",
        "fill_state": "confirmed_fill",
    }
    record_fast_live_official_reconciliation(
        engine=engine,
        result=result,
        official=official,
        observed_at=BASE + timedelta(seconds=4),
    )
    _add_evaluation(engine, "Up")
    first = settle_fast_live_position_if_resolved(
        engine=engine,
        intent_id=INTENT_ID,
        observed_at=BASE + timedelta(minutes=5, seconds=2),
    )
    second = settle_fast_live_position_if_resolved(
        engine=engine,
        intent_id=INTENT_ID,
        observed_at=BASE + timedelta(minutes=5, seconds=3),
    )
    assert first["status"] == "settled"
    assert second["status"] == "already_settled"
    assert first["reconciliation_id"] == second["reconciliation_id"]

