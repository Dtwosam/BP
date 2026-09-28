from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.engine import Engine

from bp_engine.execution.canary import (
    CANARY_INTENT_TERMINAL_EVENTS,
    CANARY_MIN_PREPARE_ARM_WINDOW_SECONDS,
    CANARY_POLICY_VERSION,
    CANARY_TARGET_NOTIONAL_USD,
    _candidate,
    _ensure_initial_reconciliation,
    _retryable_risk_reasons,
    canary_policy,
)
from bp_engine.execution.live import (
    InterlockDecision,
    _account_snapshot,
    _decimal,
    _decimal_or_negative_one,
    _optional_decimal,
    _selected_liquidity_usd,
    _source_request_matches,
    _stored_utc,
)
from bp_engine.execution.service import _draft_from_rows
from bp_engine.live_readiness.hashing import derive_id, semantic_sha256
from bp_engine.live_readiness.models import LiveRiskContext
from bp_engine.live_readiness.repository import LiveReadinessRepository
from bp_engine.live_readiness.risk import evaluate_live_risk
from bp_engine.storage import schema


def prepare_fast_live_candidate(
    *,
    engine: Engine,
    activated_at: datetime,
    observed_at: datetime,
    interlock: InterlockDecision,
    api_healthy: bool,
    official_open_order_count: int,
    collateral_balance_usd: Decimal,
) -> dict[str, object]:
    repository = LiveReadinessRepository()
    policy = canary_policy()

    with engine.begin() as connection:
        _ensure_initial_reconciliation(
            connection,
            repository=repository,
            observed_at=observed_at,
            official_open_order_count=official_open_order_count,
            collateral_balance_usd=collateral_balance_usd,
        )

        pending = connection.execute(
            select(schema.live_order_intents)
            .where(schema.live_order_intents.c.policy_version == CANARY_POLICY_VERSION)
            .order_by(schema.live_order_intents.c.id.desc())
            .limit(1)
        ).mappings().one_or_none()
        if pending is not None:
            terminal = connection.execute(
                select(schema.live_order_events.c.event_type)
                .where(
                    schema.live_order_events.c.intent_id == pending["intent_id"],
                    schema.live_order_events.c.event_type.in_(
                        CANARY_INTENT_TERMINAL_EVENTS
                    ),
                )
                .order_by(schema.live_order_events.c.id.desc())
                .limit(1)
            ).scalar_one_or_none()
            if terminal is None:
                return {
                    "status": "blocked",
                    "reason": "pending_live_intent_requires_reconciliation",
                    "intent_id": str(pending["intent_id"]),
                }

        candidate = _candidate(connection, activated_at=activated_at)
        if candidate is None:
            return {
                "status": "waiting",
                "reason": "no_new_frozen_v3_trade_order",
            }

        order, prediction = candidate
        draft = _draft_from_rows(order, prediction)
        request = draft.request
        if request.target_notional_usd != CANARY_TARGET_NOTIONAL_USD:
            raise RuntimeError("frozen paper order target changed")
        if request.target_notional_usd > policy.max_trade_size_usd:
            raise RuntimeError("frozen paper order exceeds live ceiling")
        if not _source_request_matches(prediction, request):
            raise RuntimeError("frozen paper order no longer matches source prediction")

        account = _account_snapshot(connection, observed_at=observed_at)
        selected_liquidity = _selected_liquidity_usd(
            connection,
            prediction=prediction,
            request=request,
            observed_at=observed_at,
            freshness_seconds=policy.max_prediction_age_seconds,
        )
        context = LiveRiskContext(
            prediction_id=str(prediction["prediction_id"]),
            prediction_semantic_sha256=str(prediction["semantic_sha256"]),
            recorded_at=_stored_utc(
                prediction["recorded_at"],
                "prediction.recorded_at",
            ),
            market_end_at=_stored_utc(
                prediction["market_end_at"],
                "prediction.market_end_at",
            ),
            trade=prediction["trade"] is True,
            executable=prediction["executable"] is True,
            probability=_decimal(
                prediction["calibrated_probability"],
                "probability",
            ),
            expected_edge=_decimal_or_negative_one(
                prediction["cost_adjusted_edge"]
            ),
            selected_ask=_optional_decimal(prediction["selected_ask"]),
            spread=_optional_decimal(prediction["selected_spread"]),
            selected_liquidity_usd=selected_liquidity,
            requested_notional_usd=request.target_notional_usd,
            observed_at=observed_at,
            api_healthy=api_healthy,
            duplicate_intent=False,
            account=account,
        )
        decision = evaluate_live_risk(
            policy=policy,
            context=context,
            interlock_eligible=interlock.eligible,
            interlock_reasons=interlock.reasons,
        )
        request_id = derive_id(
            "live-request",
            semantic_sha256(request.as_mapping(raw=True)),
        )
        risk_store = repository.store_risk_decision(
            connection,
            prediction_id=request.prediction_id,
            prediction_semantic_sha256=request.prediction_semantic_sha256,
            policy_version=policy.policy_version,
            decision=decision,
            account_snapshot=asdict(account),
            evidence={
                "phase": "phase15_v3_fast_live_v1",
                "request_id": request_id,
                "paper_order_id": str(order["paper_order_id"]),
                "condition_id": request.condition_id,
                "token_id": request.token_id,
                "selected_side": request.selected_side,
                "requested_notional_usd": request.target_notional_usd,
                "selected_liquidity_usd": selected_liquidity,
                "api_healthy": api_healthy,
                "official_open_order_count": official_open_order_count,
                "collateral_balance_usd": collateral_balance_usd,
                "interlock_eligible": interlock.eligible,
                "interlock_reasons": interlock.reasons,
                "fast_live": True,
            },
            created_at=observed_at,
        )
        if not decision.eligible:
            return {
                "status": "skipped",
                "reason": (
                    decision.reasons[0]
                    if decision.reasons
                    else "live_risk_blocked"
                ),
                "reasons": decision.reasons,
                "retryable": _retryable_risk_reasons(decision.reasons),
                "prediction_id": request.prediction_id,
                "paper_order_id": str(order["paper_order_id"]),
            }

        arm_window = Decimal(
            str((context.market_end_at - observed_at).total_seconds())
        )
        if arm_window < CANARY_MIN_PREPARE_ARM_WINDOW_SECONDS:
            return {
                "status": "skipped",
                "reason": "insufficient_arm_window",
                "reasons": ("insufficient_arm_window",),
                "prediction_id": request.prediction_id,
                "paper_order_id": str(order["paper_order_id"]),
                "time_to_expiry_seconds": arm_window,
            }

        intent_store = repository.store_order_intent(
            connection,
            prediction_id=request.prediction_id,
            policy_version=policy.policy_version,
            request_id=request_id,
            risk_decision_id=str(risk_store.record["decision_id"]),
            token_id=request.token_id,
            side=request.action,
            size=request.requested_shares,
            limit_price=request.limit_price,
            pre_submit_at=observed_at,
            evidence={
                "phase": "phase15_v3_fast_live_v1",
                "prediction_semantic_sha256": (
                    request.prediction_semantic_sha256
                ),
                "condition_id": request.condition_id,
                "selected_side": request.selected_side,
                "execution_version": request.execution_version,
                "execution_config_sha256": request.execution_config_sha256,
                "paper_order_id": str(order["paper_order_id"]),
                "fast_live": True,
            },
        )

    prediction_scheduled_at = _stored_utc(
        prediction["scheduled_at"],
        "prediction.scheduled_at",
    )
    prediction_recorded_at = _stored_utc(
        prediction["recorded_at"],
        "prediction.recorded_at",
    )
    paper_order_submitted_at = _stored_utc(
        order["submitted_at"],
        "paper_order.submitted_at",
    )
    market_end_at = _stored_utc(
        prediction["market_end_at"],
        "market_end_at",
    )
    timing = {
        "prediction_scheduled_at": prediction_scheduled_at.isoformat(),
        "prediction_recorded_at": prediction_recorded_at.isoformat(),
        "paper_order_submitted_at": paper_order_submitted_at.isoformat(),
        "prepared_observed_at": observed_at.isoformat(),
        "prediction_lateness_seconds": str(
            (prediction_recorded_at - prediction_scheduled_at).total_seconds()
        ),
        "paper_after_prediction_seconds": str(
            (paper_order_submitted_at - prediction_recorded_at).total_seconds()
        ),
        "prepare_after_paper_seconds": str(
            (observed_at - paper_order_submitted_at).total_seconds()
        ),
    }
    return {
        "status": "prepared",
        "intent_id": str(intent_store.record["intent_id"]),
        "request_id": request_id,
        "risk_decision_id": str(risk_store.record["decision_id"]),
        "prediction_id": request.prediction_id,
        "paper_order_id": str(order["paper_order_id"]),
        "market_end_at": market_end_at.isoformat(),
        "timing": timing,
        "request": request.as_mapping(),
        "policy": {
            "policy_version": policy.policy_version,
            "max_trade_size_usd": str(policy.max_trade_size_usd),
            "max_total_exposure_usd": str(policy.max_total_exposure_usd),
            "max_daily_loss_usd": str(policy.max_daily_loss_usd),
            "max_consecutive_losses": policy.max_consecutive_losses,
            "max_submission_attempts": 1,
        },
    }
