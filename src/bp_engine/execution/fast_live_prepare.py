from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.engine import Engine

from bp_engine.execution.fast_live import payload_sha256
from bp_engine.execution.canary import (
    CANARY_INTENT_TERMINAL_EVENTS,
    CANARY_MIN_PREPARE_ARM_WINDOW_SECONDS,
    CANARY_POLICY_VERSION,
    CANARY_TARGET_NOTIONAL_USD,
    _ensure_initial_reconciliation,
    _evaluated_prediction_ids,
    _latest_clean_account_snapshot,
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
from bp_engine.execution.models import (
    V3_FROZEN_PAPER_EXECUTION_VERSION,
    V3_FROZEN_PAPER_LATENCY_MS,
    V3_FROZEN_PAPER_ORDER_TTL_MS,
    V3_FROZEN_PAPER_SHARE_PRECISION,
    V3_FROZEN_PAPER_STARTING_CASH_USD,
    V3_FROZEN_PAPER_TARGET_NOTIONAL_USD,
    PaperExecutionConfig,
)
from bp_engine.execution.paper import PaperOrderDraft, PaperTerminalDraft, build_paper_order
from bp_engine.execution.service import derive_paper_cash
from bp_engine.features.hashing import canonical_hash
from bp_engine.live_readiness.hashing import derive_id, semantic_sha256
from bp_engine.live_readiness.models import LiveRiskContext
from bp_engine.live_readiness.repository import LiveReadinessRepository
from bp_engine.live_readiness.risk import evaluate_live_risk
from bp_engine.storage import schema


def frozen_v3_paper_config() -> PaperExecutionConfig:
    return PaperExecutionConfig(
        starting_cash_usd=V3_FROZEN_PAPER_STARTING_CASH_USD,
        target_notional_usd=V3_FROZEN_PAPER_TARGET_NOTIONAL_USD,
        latency_ms=V3_FROZEN_PAPER_LATENCY_MS,
        order_ttl_ms=V3_FROZEN_PAPER_ORDER_TTL_MS,
        share_precision=V3_FROZEN_PAPER_SHARE_PRECISION,
        execution_version=V3_FROZEN_PAPER_EXECUTION_VERSION,
        prediction_version="v3-frozen-paper-v1",
    )


def _current_frozen_paper_cash(connection) -> Decimal:
    config = frozen_v3_paper_config()
    fill_costs = connection.execute(
        select(schema.paper_fills.c.total_cost)
        .select_from(
            schema.paper_fills.join(
                schema.paper_orders,
                schema.paper_fills.c.paper_order_id
                == schema.paper_orders.c.paper_order_id,
            )
        )
        .where(
            schema.paper_orders.c.execution_version
            == V3_FROZEN_PAPER_EXECUTION_VERSION
        )
    ).scalars().all()
    payouts = connection.execute(
        select(schema.paper_settlements.c.payout)
        .select_from(
            schema.paper_settlements.join(
                schema.paper_orders,
                schema.paper_settlements.c.paper_order_id
                == schema.paper_orders.c.paper_order_id,
            )
        )
        .where(
            schema.paper_orders.c.execution_version
            == V3_FROZEN_PAPER_EXECUTION_VERSION
        )
    ).scalars().all()
    return derive_paper_cash(
        starting_cash=config.starting_cash_usd,
        fill_costs=(_decimal(value, "paper_fill_cost") for value in fill_costs),
        settlement_payouts=(
            _decimal(value, "paper_settlement_payout") for value in payouts
        ),
    )


def _prediction_candidate(
    connection,
    *,
    activated_at: datetime,
) -> dict[str, object] | None:
    evaluated = _evaluated_prediction_ids(connection)
    rows = connection.execute(
        select(schema.live_predictions)
        .where(
            schema.live_predictions.c.prediction_version
            == "v3-frozen-paper-v1",
            schema.live_predictions.c.trade.is_(True),
            schema.live_predictions.c.executable.is_(True),
            schema.live_predictions.c.recorded_at >= activated_at,
        )
        .order_by(
            schema.live_predictions.c.recorded_at,
            schema.live_predictions.c.id,
        )
    ).mappings()
    for row in rows:
        prediction_id = str(row["prediction_id"])
        if prediction_id not in evaluated:
            return dict(row)
    return None


def build_fast_live_draft(
    prediction: dict[str, object],
    *,
    available_paper_cash: Decimal,
) -> tuple[str, PaperOrderDraft]:
    draft = build_paper_order(
        prediction,
        frozen_v3_paper_config(),
        available_paper_cash,
    )
    if isinstance(draft, PaperTerminalDraft):
        raise RuntimeError(
            f"frozen V3 prediction cannot build live-equivalent order: {draft.reason}"
        )
    paper_order_id = canonical_hash(
        {
            "prediction_id": draft.request.prediction_id,
            "execution_version": draft.request.execution_version,
        }
    )
    return paper_order_id, draft


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

        prediction = _prediction_candidate(
            connection,
            activated_at=activated_at,
        )
        if prediction is None:
            return {
                "status": "waiting",
                "reason": "no_new_frozen_v3_trade_prediction",
            }

        available_paper_cash = _current_frozen_paper_cash(connection)
        paper_order_id, draft = build_fast_live_draft(
            prediction,
            available_paper_cash=available_paper_cash,
        )
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
                "paper_order_id": paper_order_id,
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
                "paper_order_id": paper_order_id,
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
                "paper_order_id": paper_order_id,
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
                "paper_order_id": paper_order_id,
                "request_sha256": payload_sha256(request.as_mapping()),
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
    paper_order_submitted_at = request.submitted_at
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
        "fast_live_order_derived_directly_from_prediction": True,
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
        "paper_order_id": paper_order_id,
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

def record_fast_live_result(
    *,
    engine: Engine,
    result: dict[str, object],
    observed_at: datetime,
) -> dict[str, object]:
    intent_id = str(result.get("intent_id") or "").strip()
    request_hash = str(result.get("request_sha256") or "").strip()
    status = str(result.get("status") or "").strip()
    if not intent_id or not request_hash or not status:
        raise RuntimeError("fast live result binding missing")

    network_attempt = result.get("network_submission_attempt_consumed") is True
    real_order_submitted = result.get("real_order_submitted") is True
    if not network_attempt and real_order_submitted:
        raise RuntimeError("fast live result claims order without network attempt")

    repository = LiveReadinessRepository()
    with engine.begin() as connection:
        intent = connection.execute(
            select(schema.live_order_intents).where(
                schema.live_order_intents.c.intent_id == intent_id,
                schema.live_order_intents.c.policy_version == CANARY_POLICY_VERSION,
            )
        ).mappings().one_or_none()
        if intent is None:
            raise RuntimeError("unknown fast live intent")

        if network_attempt:
            existing = connection.execute(
                select(schema.live_order_events.c.event_type)
                .where(
                    schema.live_order_events.c.intent_id == intent_id,
                    schema.live_order_events.c.event_type.in_(
                        ("accepted", "rejected", "submission_unknown")
                    ),
                )
                .order_by(schema.live_order_events.c.id.desc())
                .limit(1)
            ).scalar_one_or_none()
            if existing is not None:
                return {
                    "status": "already_recorded",
                    "intent_id": intent_id,
                    "event_type": str(existing),
                    "network_submission_attempt_consumed": True,
                    "recycle_allowed": False,
                }

            accepted = result.get("accepted") is True
            external_order_id = (
                str(result.get("external_order_id") or "").strip() or None
            )
            if accepted and external_order_id:
                event_type = "accepted"
            elif status == "rejected":
                event_type = "rejected"
            else:
                event_type = "submission_unknown"

            repository.store_order_event(
                connection,
                event_key=f"{intent_id}:{event_type}",
                intent_id=intent_id,
                event_type=event_type,
                observed_at=observed_at,
                external_order_id=external_order_id,
                external_trade_id=None,
                evidence={
                    "phase": "phase15_v3_fast_live_v1",
                    "request_sha256": request_hash,
                    "executor_result": result,
                    "network_submission_attempt_consumed": True,
                    "real_order_submitted": real_order_submitted,
                },
            )

            cancellation = result.get("cancellation")
            if accepted and external_order_id and isinstance(cancellation, dict):
                if cancellation.get("cancelled") is True:
                    cancel_event = "cancelled"
                elif str(cancellation.get("not_cancelled") or "").strip():
                    cancel_event = "cancel_rejected"
                else:
                    cancel_event = "cancellation_unknown"
                repository.store_order_event(
                    connection,
                    event_key=f"{intent_id}:{cancel_event}",
                    intent_id=intent_id,
                    event_type=cancel_event,
                    observed_at=observed_at,
                    external_order_id=external_order_id,
                    external_trade_id=None,
                    evidence={
                        "phase": "phase15_v3_fast_live_v1",
                        "request_sha256": request_hash,
                        "cancellation": cancellation,
                        "ttl_seconds": 2,
                    },
                )

            return {
                "status": "recorded_network_attempt",
                "intent_id": intent_id,
                "event_type": event_type,
                "external_order_id": external_order_id,
                "network_submission_attempt_consumed": True,
                "recycle_allowed": False,
            }

        existing_closed = connection.execute(
            select(schema.live_order_events.c.event_type)
            .where(
                schema.live_order_events.c.intent_id == intent_id,
                schema.live_order_events.c.event_type == "closed_before_submission",
            )
            .order_by(schema.live_order_events.c.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if existing_closed is not None:
            return {
                "status": "already_closed_before_submission",
                "intent_id": intent_id,
                "event_type": "closed_before_submission",
                "network_submission_attempt_consumed": False,
                "recycle_allowed": True,
            }

        attempted = connection.execute(
            select(schema.live_order_events.c.event_type)
            .where(
                schema.live_order_events.c.intent_id == intent_id,
                schema.live_order_events.c.event_type.in_(
                    ("accepted", "rejected", "submission_unknown")
                ),
            )
            .order_by(schema.live_order_events.c.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if attempted is not None:
            raise RuntimeError("cannot recycle fast live intent after network attempt")

        carried = _latest_clean_account_snapshot(
            connection,
            observed_at=observed_at,
        )
        repository.store_order_event(
            connection,
            event_key=f"{intent_id}:closed_before_submission",
            intent_id=intent_id,
            event_type="closed_before_submission",
            observed_at=observed_at,
            external_order_id=None,
            external_trade_id=None,
            evidence={
                "phase": "phase15_v3_fast_live_v1",
                "reason": status,
                "request_sha256": request_hash,
                "executor_result": result,
                "network_submission_attempt_consumed": False,
                "real_order_submitted": False,
            },
        )
        reconciliation = repository.store_reconciliation_run(
            connection,
            observed_at=observed_at,
            unresolved_count=0,
            critical_count=0,
            evidence={
                "phase": "phase15_v3_fast_live_v1",
                "intent_id": intent_id,
                "reconciliation_kind": "fast_live_pre_submission_close",
                "reason": status,
                "request_sha256": request_hash,
                "network_submission_attempt_consumed": False,
                **(
                    {
                        "account_snapshot_carried_from_reconciliation_id": carried[0],
                        "account_snapshot": carried[1],
                    }
                    if carried is not None
                    else {}
                ),
            },
        )
        return {
            "status": "closed_before_submission",
            "intent_id": intent_id,
            "event_type": "closed_before_submission",
            "reconciliation_id": str(reconciliation.record["reconciliation_id"]),
            "network_submission_attempt_consumed": False,
            "recycle_allowed": True,
        }

