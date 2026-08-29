from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import Connection, select
from sqlalchemy.engine import Engine

from bp_engine.execution.live_client import LiveTradingClient
from bp_engine.execution.models import (
    ExecutionCancelAck,
    ExecutionOrderAck,
    ExecutionOrderRequest,
)
from bp_engine.live_prediction.repository import LivePredictionRepository
from bp_engine.live_readiness.hashing import derive_id, semantic_sha256
from bp_engine.live_readiness.models import (
    LiveAccountSnapshot,
    LiveRiskContext,
    LiveRiskPolicy,
)
from bp_engine.live_readiness.repository import LiveReadinessRepository
from bp_engine.live_readiness.risk import evaluate_live_risk
from bp_engine.storage import schema


@dataclass(frozen=True)
class InterlockDecision:
    eligible: bool
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        normalized: list[str] = []
        for reason in self.reasons:
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError("interlock reasons must contain nonblank strings")
            value = reason.strip()
            if value not in normalized:
                normalized.append(value)
        if self.eligible and normalized:
            raise ValueError("eligible interlock decision cannot contain blocking reasons")
        object.__setattr__(self, "reasons", tuple(normalized))


class PolymarketLiveExecutionGateway:
    """Fail-closed live gateway with durable pre-submit evidence and retry safety."""

    def __init__(
        self,
        *,
        engine: Engine,
        repository: LiveReadinessRepository,
        policy: LiveRiskPolicy,
        client_factory: Callable[[], LiveTradingClient],
        interlock: Callable[[datetime], InterlockDecision],
        api_health: Callable[[], bool],
        now: Callable[[], datetime],
    ) -> None:
        self._engine = engine
        self._repository = repository
        self._policy = policy
        self._client_factory = client_factory
        self._interlock = interlock
        self._api_health = api_health
        self._now = now
        self._predictions = LivePredictionRepository()

    def submit_order(self, request: ExecutionOrderRequest) -> ExecutionOrderAck:
        observed_at = _utc(self._now(), "now")
        request_id = derive_id(
            "live-request",
            semantic_sha256(request.as_mapping(raw=True)),
        )

        with self._engine.begin() as connection:
            prediction = self._predictions.get_by_id(connection, request.prediction_id)
            if not _source_request_matches(prediction, request):
                return ExecutionOrderAck(
                    order_id=request_id,
                    accepted=False,
                    observed_at=observed_at,
                    reason="source_prediction_mismatch",
                )

            existing_intent = _intent_for_prediction(
                connection,
                prediction_id=request.prediction_id,
                policy_version=self._policy.policy_version,
            )
            if existing_intent is not None:
                replay = _replay_submission_ack(
                    connection,
                    intent=existing_intent,
                    observed_at=observed_at,
                )
                if replay is not None:
                    return replay

            account = _account_snapshot(connection, observed_at=observed_at)
            selected_liquidity = _selected_liquidity_usd(
                connection,
                prediction=prediction,
                request=request,
                observed_at=observed_at,
                freshness_seconds=self._policy.max_prediction_age_seconds,
            )
            interlock = _safe_interlock(self._interlock, observed_at)
            api_healthy = _safe_api_health(self._api_health)
            context = LiveRiskContext(
                prediction_id=str(prediction["prediction_id"]),
                prediction_semantic_sha256=str(prediction["semantic_sha256"]),
                recorded_at=_utc(prediction["recorded_at"], "prediction.recorded_at"),
                market_end_at=_utc(prediction["market_end_at"], "prediction.market_end_at"),
                trade=prediction["trade"] is True,
                executable=prediction["executable"] is True,
                probability=_decimal(prediction["calibrated_probability"], "probability"),
                expected_edge=_decimal_or_negative_one(prediction["cost_adjusted_edge"]),
                selected_ask=_optional_decimal(prediction["selected_ask"]),
                spread=_optional_decimal(prediction["selected_spread"]),
                selected_liquidity_usd=selected_liquidity,
                requested_notional_usd=request.target_notional_usd,
                observed_at=observed_at,
                api_healthy=api_healthy,
                duplicate_intent=existing_intent is not None,
                account=account,
            )
            decision = evaluate_live_risk(
                policy=self._policy,
                context=context,
                interlock_eligible=interlock.eligible,
                interlock_reasons=interlock.reasons,
            )
            risk_store = self._repository.store_risk_decision(
                connection,
                prediction_id=request.prediction_id,
                prediction_semantic_sha256=request.prediction_semantic_sha256,
                policy_version=self._policy.policy_version,
                decision=decision,
                account_snapshot=asdict(account),
                evidence={
                    "request_id": request_id,
                    "condition_id": request.condition_id,
                    "token_id": request.token_id,
                    "selected_side": request.selected_side,
                    "requested_notional_usd": request.target_notional_usd,
                    "selected_liquidity_usd": selected_liquidity,
                    "api_healthy": api_healthy,
                    "interlock_eligible": interlock.eligible,
                    "interlock_reasons": interlock.reasons,
                    "duplicate_intent": existing_intent is not None,
                },
                created_at=observed_at,
            )
            if not decision.eligible:
                return ExecutionOrderAck(
                    order_id=request_id,
                    accepted=False,
                    observed_at=observed_at,
                    reason=decision.reasons[0] if decision.reasons else "live_risk_blocked",
                )

            intent_store = self._repository.store_order_intent(
                connection,
                prediction_id=request.prediction_id,
                policy_version=self._policy.policy_version,
                request_id=request_id,
                risk_decision_id=str(risk_store.record["decision_id"]),
                token_id=request.token_id,
                side=request.action,
                size=request.requested_shares,
                limit_price=request.limit_price,
                pre_submit_at=observed_at,
                evidence={
                    "prediction_semantic_sha256": request.prediction_semantic_sha256,
                    "condition_id": request.condition_id,
                    "selected_side": request.selected_side,
                    "execution_version": request.execution_version,
                    "execution_config_sha256": request.execution_config_sha256,
                },
            )
            intent_id = str(intent_store.record["intent_id"])

        try:
            client = self._client_factory()
            result = client.submit_limit_buy(
                token_id=request.token_id,
                price=request.limit_price,
                size=request.requested_shares,
            )
        except Exception:
            self._store_submission_event(
                intent_id=intent_id,
                event_type="submission_unknown",
                observed_at=observed_at,
                external_order_id=None,
                evidence={"status": "submission_unknown", "code": "client_exception"},
            )
            return ExecutionOrderAck(
                order_id=intent_id,
                accepted=False,
                observed_at=observed_at,
                reason="submission_unknown",
            )

        if result.accepted and result.external_order_id:
            self._store_submission_event(
                intent_id=intent_id,
                event_type="accepted",
                observed_at=observed_at,
                external_order_id=result.external_order_id,
                evidence={
                    "status": result.status,
                    "code": result.code,
                    "message": result.message,
                },
            )
            return ExecutionOrderAck(
                order_id=result.external_order_id,
                accepted=True,
                observed_at=observed_at,
                reason="accepted",
            )

        if not result.accepted and result.status.lower() == "rejected":
            self._store_submission_event(
                intent_id=intent_id,
                event_type="rejected",
                observed_at=observed_at,
                external_order_id=result.external_order_id,
                evidence={
                    "status": result.status,
                    "code": result.code,
                    "message": result.message,
                },
            )
            return ExecutionOrderAck(
                order_id=intent_id,
                accepted=False,
                observed_at=observed_at,
                reason=result.code or "rejected",
            )

        self._store_submission_event(
            intent_id=intent_id,
            event_type="submission_unknown",
            observed_at=observed_at,
            external_order_id=result.external_order_id,
            evidence={"status": result.status, "code": result.code},
        )
        return ExecutionOrderAck(
            order_id=intent_id,
            accepted=False,
            observed_at=observed_at,
            reason="submission_unknown",
        )

    def cancel_order(self, order_id: str, observed_at: datetime) -> ExecutionCancelAck:
        observed = _utc(observed_at, "observed_at")
        with self._engine.begin() as connection:
            accepted = connection.execute(
                select(schema.live_order_events)
                .where(
                    schema.live_order_events.c.external_order_id == order_id,
                    schema.live_order_events.c.event_type == "accepted",
                )
                .order_by(schema.live_order_events.c.id.desc())
                .limit(1)
            ).mappings().one_or_none()
            if accepted is None:
                return ExecutionCancelAck(
                    order_id=order_id,
                    cancelled=False,
                    observed_at=observed,
                    reason="unknown_order_id",
                )
            intent_id = str(accepted["intent_id"])
            existing_cancel = connection.execute(
                select(schema.live_order_events)
                .where(
                    schema.live_order_events.c.intent_id == intent_id,
                    schema.live_order_events.c.event_type == "cancelled",
                )
                .order_by(schema.live_order_events.c.id.desc())
                .limit(1)
            ).mappings().one_or_none()
            if existing_cancel is not None:
                return ExecutionCancelAck(
                    order_id=order_id,
                    cancelled=True,
                    observed_at=observed,
                    reason="already_cancelled",
                )

        try:
            client = self._client_factory()
            result = client.cancel(external_order_id=order_id)
        except Exception:
            self._store_submission_event(
                intent_id=intent_id,
                event_type="cancellation_unknown",
                observed_at=observed,
                external_order_id=order_id,
                evidence={"status": "cancellation_unknown", "code": "client_exception"},
            )
            return ExecutionCancelAck(
                order_id=order_id,
                cancelled=False,
                observed_at=observed,
                reason="cancellation_unknown",
            )

        if result.cancelled:
            self._store_submission_event(
                intent_id=intent_id,
                event_type="cancelled",
                observed_at=observed,
                external_order_id=order_id,
                evidence={"status": result.status, "message": result.message},
            )
            return ExecutionCancelAck(
                order_id=order_id,
                cancelled=True,
                observed_at=observed,
                reason="cancelled",
            )

        event_type = "cancel_rejected" if result.status == "not_cancelled" else "cancellation_unknown"
        self._store_submission_event(
            intent_id=intent_id,
            event_type=event_type,
            observed_at=observed,
            external_order_id=order_id,
            evidence={"status": result.status, "message": result.message},
        )
        return ExecutionCancelAck(
            order_id=order_id,
            cancelled=False,
            observed_at=observed,
            reason=result.status or "cancellation_unknown",
        )

    def _store_submission_event(
        self,
        *,
        intent_id: str,
        event_type: str,
        observed_at: datetime,
        external_order_id: str | None,
        evidence: dict[str, Any],
    ) -> None:
        event_key = f"{intent_id}:{event_type}"
        with self._engine.begin() as connection:
            self._repository.store_order_event(
                connection,
                event_key=event_key,
                intent_id=intent_id,
                event_type=event_type,
                observed_at=observed_at,
                external_order_id=external_order_id,
                external_trade_id=None,
                evidence=evidence,
            )


def _source_request_matches(
    prediction: Any,
    request: ExecutionOrderRequest,
) -> bool:
    if prediction is None:
        return False
    try:
        selected_side = str(prediction["selected_side"]).lower()
        token_key = "up_token_id" if selected_side == "up" else "down_token_id"
        expected_token = str(prediction[token_key])
        selected_ask = _optional_decimal(prediction["selected_ask"])
        recorded_at = _utc(prediction["recorded_at"], "prediction.recorded_at")
        market_end_at = _utc(prediction["market_end_at"], "prediction.market_end_at")
    except (KeyError, TypeError, ValueError):
        return False
    return (
        str(prediction["prediction_id"]) == request.prediction_id
        and str(prediction["semantic_sha256"]) == request.prediction_semantic_sha256
        and str(prediction["condition_id"]) == request.condition_id
        and selected_side == request.selected_side
        and expected_token == request.token_id
        and request.action == "BUY"
        and request.submitted_at == recorded_at
        and request.expires_at <= market_end_at
        and selected_ask is not None
        and request.limit_price >= selected_ask
        and request.requested_shares * request.limit_price <= request.target_notional_usd
    )


def _intent_for_prediction(
    connection: Connection,
    *,
    prediction_id: str,
    policy_version: str,
) -> Any:
    return connection.execute(
        select(schema.live_order_intents).where(
            schema.live_order_intents.c.prediction_id == prediction_id,
            schema.live_order_intents.c.policy_version == policy_version,
        )
    ).mappings().one_or_none()


def _replay_submission_ack(
    connection: Connection,
    *,
    intent: Any,
    observed_at: datetime,
) -> ExecutionOrderAck | None:
    event = connection.execute(
        select(schema.live_order_events)
        .where(
            schema.live_order_events.c.intent_id == intent["intent_id"],
            schema.live_order_events.c.event_type.in_(
                ("accepted", "rejected", "submission_unknown")
            ),
        )
        .order_by(schema.live_order_events.c.id.desc())
        .limit(1)
    ).mappings().one_or_none()
    if event is None:
        return None
    event_type = str(event["event_type"])
    if event_type == "accepted" and event["external_order_id"]:
        return ExecutionOrderAck(
            order_id=str(event["external_order_id"]),
            accepted=True,
            observed_at=observed_at,
            reason="existing",
        )
    if event_type == "rejected":
        evidence = dict(event["evidence"] or {})
        return ExecutionOrderAck(
            order_id=str(intent["intent_id"]),
            accepted=False,
            observed_at=observed_at,
            reason=str(evidence.get("code") or "rejected"),
        )
    return ExecutionOrderAck(
        order_id=str(intent["intent_id"]),
        accepted=False,
        observed_at=observed_at,
        reason="submission_unknown",
    )


def _account_snapshot(connection: Connection, *, observed_at: datetime) -> LiveAccountSnapshot:
    intents = connection.execute(
        select(schema.live_order_intents).where(
            schema.live_order_intents.c.pre_submit_at <= observed_at
        )
    ).mappings().all()
    last_order_at = None
    exposure = Decimal("0")
    for intent in intents:
        pre_submit_at = _utc(intent["pre_submit_at"], "intent.pre_submit_at")
        if last_order_at is None or pre_submit_at > last_order_at:
            last_order_at = pre_submit_at
        outcome = connection.execute(
            select(schema.live_order_events.c.event_type)
            .where(
                schema.live_order_events.c.intent_id == intent["intent_id"],
                schema.live_order_events.c.event_type.in_(
                    ("accepted", "rejected", "submission_unknown")
                ),
            )
            .order_by(schema.live_order_events.c.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if outcome != "rejected":
            exposure += _decimal(intent["size"], "intent.size") * _decimal(
                intent["limit_price"], "intent.limit_price"
            )

    reconciliation = connection.execute(
        select(schema.live_reconciliation_runs)
        .where(schema.live_reconciliation_runs.c.observed_at <= observed_at)
        .order_by(
            schema.live_reconciliation_runs.c.observed_at.desc(),
            schema.live_reconciliation_runs.c.id.desc(),
        )
        .limit(1)
    ).mappings().one_or_none()
    if reconciliation is None:
        return LiveAccountSnapshot(
            total_exposure_usd=exposure,
            realized_daily_pnl_usd=Decimal("0"),
            consecutive_losses=0,
            last_order_at=last_order_at,
            unresolved_critical_reconciliation=1,
        )

    evidence = dict(reconciliation["evidence"] or {})
    raw_account = evidence.get("account_snapshot")
    account_evidence = dict(raw_account) if isinstance(raw_account, dict) else {}
    evidence_exposure = _safe_nonnegative_decimal(
        account_evidence.get("total_exposure_usd"),
        default=exposure,
    )
    total_exposure = max(exposure, evidence_exposure)
    realized_pnl = _safe_decimal(
        account_evidence.get("realized_daily_pnl_usd"),
        default=Decimal("0"),
    )
    consecutive_losses = _safe_nonnegative_int(
        account_evidence.get("consecutive_losses"),
        default=0,
    )
    critical_count = int(reconciliation["critical_count"])
    if intents and not account_evidence:
        critical_count = max(critical_count, 1)
    return LiveAccountSnapshot(
        total_exposure_usd=total_exposure,
        realized_daily_pnl_usd=realized_pnl,
        consecutive_losses=consecutive_losses,
        last_order_at=last_order_at,
        unresolved_critical_reconciliation=critical_count,
    )


def _selected_liquidity_usd(
    connection: Connection,
    *,
    prediction: Any,
    request: ExecutionOrderRequest,
    observed_at: datetime,
    freshness_seconds: Decimal,
) -> Decimal | None:
    state = connection.execute(
        select(schema.market_state_1s)
        .where(
            schema.market_state_1s.c.source == "polymarket",
            schema.market_state_1s.c.stream == "market",
            schema.market_state_1s.c.instrument == request.condition_id,
            schema.market_state_1s.c.asset_id == request.token_id,
            schema.market_state_1s.c.bucket_at <= observed_at,
            schema.market_state_1s.c.last_event_at <= observed_at,
        )
        .order_by(
            schema.market_state_1s.c.bucket_at.desc(),
            schema.market_state_1s.c.id.desc(),
        )
        .limit(1)
    ).mappings().one_or_none()
    if state is None:
        return None
    last_event_at = _utc(state["last_event_at"], "state.last_event_at")
    age_seconds = Decimal(str((observed_at - last_event_at).total_seconds()))
    if age_seconds < 0 or age_seconds > freshness_seconds:
        return None
    payload = dict(state["state"] or {})
    try:
        best_ask = _decimal(payload.get("best_ask"), "state.best_ask")
        ask_depth = _decimal(payload.get("ask_depth"), "state.ask_depth")
    except ValueError:
        return None
    if best_ask <= 0 or ask_depth < 0:
        return None
    source_selected_ask = _optional_decimal(prediction["selected_ask"])
    if source_selected_ask is None or best_ask > request.limit_price:
        return None
    return best_ask * ask_depth


def _safe_interlock(
    callback: Callable[[datetime], InterlockDecision],
    observed_at: datetime,
) -> InterlockDecision:
    try:
        decision = callback(observed_at)
    except Exception:
        return InterlockDecision(eligible=False, reasons=("live_interlock_error",))
    if not isinstance(decision, InterlockDecision):
        return InterlockDecision(eligible=False, reasons=("live_interlock_error",))
    return decision


def _safe_api_health(callback: Callable[[], bool]) -> bool:
    try:
        return callback() is True
    except Exception:
        return False


def _utc(value: Any, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _decimal(value: Any, name: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    try:
        numeric = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not numeric.is_finite():
        raise ValueError(f"{name} must be finite")
    return numeric


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return _decimal(value, "value")
    except ValueError:
        return None


def _decimal_or_negative_one(value: Any) -> Decimal:
    result = _optional_decimal(value)
    return result if result is not None else Decimal("-1")


def _safe_decimal(value: Any, *, default: Decimal) -> Decimal:
    try:
        return _decimal(value, "value")
    except ValueError:
        return default


def _safe_nonnegative_decimal(value: Any, *, default: Decimal) -> Decimal:
    result = _safe_decimal(value, default=default)
    return result if result >= 0 else default


def _safe_nonnegative_int(value: Any, *, default: int) -> int:
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return default
    return numeric if numeric >= 0 else default
