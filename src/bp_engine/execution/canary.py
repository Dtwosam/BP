from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

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
from bp_engine.live_readiness.models import LiveRiskContext, LiveRiskPolicy
from bp_engine.live_readiness.repository import LiveReadinessRepository
from bp_engine.live_readiness.risk import evaluate_live_risk
from bp_engine.storage import schema
from bp_engine.v3_paper.service import (
    V3_PAPER_EXECUTION_VERSION,
    V3_PAPER_PREDICTION_VERSION,
)

CANARY_POLICY_VERSION = "v3-live-canary-v1"
CANARY_MAX_TRADE_SIZE_USD = Decimal("10")
CANARY_MAX_TOTAL_EXPOSURE_USD = Decimal("10")
CANARY_MAX_DAILY_LOSS_USD = Decimal("10")
CANARY_MAX_CONSECUTIVE_LOSSES = 1
CANARY_MIN_EDGE = Decimal("0.075")
CANARY_MIN_PROBABILITY = Decimal("0")
CANARY_MIN_LIQUIDITY_USD = Decimal("1")
CANARY_MAX_SPREAD = Decimal("0.10")
CANARY_MAX_PREDICTION_AGE_SECONDS = Decimal("30")
CANARY_MIN_TIME_TO_EXPIRY_SECONDS = Decimal("15")
CANARY_COOLDOWN_SECONDS = Decimal("86400")
CANARY_MAX_ACCEPTED_ORDERS = 1


def canary_policy() -> LiveRiskPolicy:
    return LiveRiskPolicy(
        max_trade_size_usd=CANARY_MAX_TRADE_SIZE_USD,
        max_total_exposure_usd=CANARY_MAX_TOTAL_EXPOSURE_USD,
        max_daily_loss_usd=CANARY_MAX_DAILY_LOSS_USD,
        max_consecutive_losses=CANARY_MAX_CONSECUTIVE_LOSSES,
        min_edge=CANARY_MIN_EDGE,
        min_probability=CANARY_MIN_PROBABILITY,
        min_liquidity_usd=CANARY_MIN_LIQUIDITY_USD,
        max_spread=CANARY_MAX_SPREAD,
        max_prediction_age_seconds=CANARY_MAX_PREDICTION_AGE_SECONDS,
        min_time_to_expiry_seconds=CANARY_MIN_TIME_TO_EXPIRY_SECONDS,
        cooldown_seconds=CANARY_COOLDOWN_SECONDS,
        policy_version=CANARY_POLICY_VERSION,
    )


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _accepted_count(connection) -> int:
    return int(
        connection.scalar(
            select(func.count())
            .select_from(
                schema.live_order_events.join(
                    schema.live_order_intents,
                    schema.live_order_events.c.intent_id
                    == schema.live_order_intents.c.intent_id,
                )
            )
            .where(
                schema.live_order_intents.c.policy_version == CANARY_POLICY_VERSION,
                schema.live_order_events.c.event_type == "accepted",
            )
        )
        or 0
    )


def _ensure_initial_reconciliation(
    connection,
    *,
    repository: LiveReadinessRepository,
    observed_at: datetime,
) -> None:
    live_intent_count = int(
        connection.scalar(select(func.count()).select_from(schema.live_order_intents)) or 0
    )
    if live_intent_count:
        return
    latest = connection.execute(
        select(schema.live_reconciliation_runs.c.reconciliation_id)
        .order_by(
            schema.live_reconciliation_runs.c.observed_at.desc(),
            schema.live_reconciliation_runs.c.id.desc(),
        )
        .limit(1)
    ).scalar_one_or_none()
    if latest is not None:
        return
    repository.store_reconciliation_run(
        connection,
        observed_at=observed_at,
        unresolved_count=0,
        critical_count=0,
        evidence={
            "source": "phase15_v3_live_canary_initial_zero_order_baseline",
            "account_snapshot": {
                "total_exposure_usd": "0",
                "realized_daily_pnl_usd": "0",
                "consecutive_losses": 0,
            },
        },
    )


def _evaluated_prediction_ids(connection) -> set[str]:
    rows = connection.execute(
        select(schema.live_risk_decisions.c.prediction_id).where(
            schema.live_risk_decisions.c.policy_version == CANARY_POLICY_VERSION
        )
    ).scalars()
    return {str(value) for value in rows}


def _candidate(
    connection,
    *,
    activated_at: datetime,
) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    evaluated = _evaluated_prediction_ids(connection)
    query = (
        select(schema.paper_orders)
        .where(
            schema.paper_orders.c.execution_version == V3_PAPER_EXECUTION_VERSION,
            schema.paper_orders.c.created_at >= activated_at,
        )
        .order_by(schema.paper_orders.c.created_at, schema.paper_orders.c.id)
    )
    for row in connection.execute(query).mappings():
        prediction_id = str(row["prediction_id"])
        if prediction_id in evaluated:
            continue
        prediction = connection.execute(
            select(schema.live_predictions).where(
                schema.live_predictions.c.prediction_id == prediction_id,
                schema.live_predictions.c.prediction_version == V3_PAPER_PREDICTION_VERSION,
                schema.live_predictions.c.trade.is_(True),
                schema.live_predictions.c.executable.is_(True),
            )
        ).mappings().one_or_none()
        if prediction is None:
            continue
        return dict(row), dict(prediction)
    return None


def prepare_next_canary(
    *,
    engine: Engine,
    activated_at: datetime,
    observed_at: datetime,
    interlock: InterlockDecision,
    api_healthy: bool,
) -> dict[str, object]:
    activated = _utc(activated_at, "activated_at")
    observed = _utc(observed_at, "observed_at")
    repository = LiveReadinessRepository()
    policy = canary_policy()

    with engine.begin() as connection:
        _ensure_initial_reconciliation(
            connection,
            repository=repository,
            observed_at=observed,
        )

        accepted_count = _accepted_count(connection)
        if accepted_count >= CANARY_MAX_ACCEPTED_ORDERS:
            return {
                "status": "stopped",
                "reason": "canary_accepted_order_limit_reached",
                "accepted_order_count": accepted_count,
            }

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
                        ("accepted", "rejected", "submission_unknown")
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

        candidate = _candidate(connection, activated_at=activated)
        if candidate is None:
            return {"status": "waiting", "reason": "no_new_frozen_v3_trade_order"}

        order, prediction = candidate
        draft = _draft_from_rows(order, prediction)
        request = draft.request
        if request.target_notional_usd > CANARY_MAX_TRADE_SIZE_USD:
            raise RuntimeError("frozen paper order exceeds authorized canary ceiling")
        if not _source_request_matches(prediction, request):
            raise RuntimeError("frozen paper order no longer matches source prediction")

        account = _account_snapshot(connection, observed_at=observed)
        selected_liquidity = _selected_liquidity_usd(
            connection,
            prediction=prediction,
            request=request,
            observed_at=observed,
            freshness_seconds=policy.max_prediction_age_seconds,
        )
        context = LiveRiskContext(
            prediction_id=str(prediction["prediction_id"]),
            prediction_semantic_sha256=str(prediction["semantic_sha256"]),
            recorded_at=_stored_utc(prediction["recorded_at"], "prediction.recorded_at"),
            market_end_at=_stored_utc(prediction["market_end_at"], "prediction.market_end_at"),
            trade=prediction["trade"] is True,
            executable=prediction["executable"] is True,
            probability=_decimal(prediction["calibrated_probability"], "probability"),
            expected_edge=_decimal_or_negative_one(prediction["cost_adjusted_edge"]),
            selected_ask=_optional_decimal(prediction["selected_ask"]),
            spread=_optional_decimal(prediction["selected_spread"]),
            selected_liquidity_usd=selected_liquidity,
            requested_notional_usd=request.target_notional_usd,
            observed_at=observed,
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
                "phase": "phase15_v3_live_canary_v1",
                "request_id": request_id,
                "paper_order_id": str(order["paper_order_id"]),
                "condition_id": request.condition_id,
                "token_id": request.token_id,
                "selected_side": request.selected_side,
                "requested_notional_usd": request.target_notional_usd,
                "selected_liquidity_usd": selected_liquidity,
                "api_healthy": api_healthy,
                "interlock_eligible": interlock.eligible,
                "interlock_reasons": interlock.reasons,
            },
            created_at=observed,
        )
        if not decision.eligible:
            return {
                "status": "skipped",
                "reason": decision.reasons[0] if decision.reasons else "live_risk_blocked",
                "reasons": decision.reasons,
                "prediction_id": request.prediction_id,
                "paper_order_id": str(order["paper_order_id"]),
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
            pre_submit_at=observed,
            evidence={
                "phase": "phase15_v3_live_canary_v1",
                "prediction_semantic_sha256": request.prediction_semantic_sha256,
                "condition_id": request.condition_id,
                "selected_side": request.selected_side,
                "execution_version": request.execution_version,
                "execution_config_sha256": request.execution_config_sha256,
                "paper_order_id": str(order["paper_order_id"]),
            },
        )

    return {
        "status": "prepared",
        "intent_id": str(intent_store.record["intent_id"]),
        "request_id": request_id,
        "prediction_id": request.prediction_id,
        "paper_order_id": str(order["paper_order_id"]),
        "market_end_at": _stored_utc(prediction["market_end_at"], "market_end_at").isoformat(),
        "request": request.as_mapping(),
        "policy": {
            "policy_version": policy.policy_version,
            "max_trade_size_usd": str(policy.max_trade_size_usd),
            "max_total_exposure_usd": str(policy.max_total_exposure_usd),
            "max_daily_loss_usd": str(policy.max_daily_loss_usd),
            "max_consecutive_losses": policy.max_consecutive_losses,
        },
    }


def record_canary_submission(
    *,
    engine: Engine,
    intent_id: str,
    observed_at: datetime,
    result: Mapping[str, object],
) -> dict[str, object]:
    observed = _utc(observed_at, "observed_at")
    repository = LiveReadinessRepository()
    accepted = result.get("accepted") is True
    status = str(result.get("status") or "")
    code = str(result.get("code") or "")
    external_order_id = str(result.get("external_order_id") or "") or None

    if accepted and external_order_id:
        event_type = "accepted"
    elif status.lower() == "rejected":
        event_type = "rejected"
    else:
        event_type = "submission_unknown"

    with engine.begin() as connection:
        intent = connection.execute(
            select(schema.live_order_intents).where(
                schema.live_order_intents.c.intent_id == intent_id,
                schema.live_order_intents.c.policy_version == CANARY_POLICY_VERSION,
            )
        ).mappings().one_or_none()
        if intent is None:
            raise RuntimeError("unknown canary intent")

        repository.store_order_event(
            connection,
            event_key=f"{intent_id}:{event_type}",
            intent_id=intent_id,
            event_type=event_type,
            observed_at=observed,
            external_order_id=external_order_id,
            external_trade_id=None,
            evidence={
                "phase": "phase15_v3_live_canary_v1",
                "status": status,
                "code": code,
                "message": str(result.get("message") or ""),
                "remote_geoblock": result.get("geoblock"),
            },
        )

        cancellation = result.get("cancellation")
        if accepted and external_order_id and isinstance(cancellation, Mapping):
            cancelled = cancellation.get("cancelled") is True
            cancel_status = str(cancellation.get("status") or "")
            if cancelled:
                cancel_event_type = "cancelled"
            elif cancel_status == "not_cancelled":
                cancel_event_type = "cancel_rejected"
            else:
                cancel_event_type = "cancellation_unknown"
            repository.store_order_event(
                connection,
                event_key=f"{intent_id}:{cancel_event_type}",
                intent_id=intent_id,
                event_type=cancel_event_type,
                observed_at=observed,
                external_order_id=external_order_id,
                external_trade_id=None,
                evidence={
                    "phase": "phase15_v3_live_canary_v1",
                    "status": cancel_status,
                    "message": str(cancellation.get("message") or ""),
                    "ttl_seconds": 2,
                },
            )

    return {
        "status": "recorded",
        "intent_id": intent_id,
        "event_type": event_type,
        "external_order_id": external_order_id,
        "accepted": accepted,
        "canary_complete": accepted,
    }
