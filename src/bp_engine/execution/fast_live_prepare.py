from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select, true
from sqlalchemy.engine import Engine

from bp_engine.execution.canary import (
    CANARY_INTENT_TERMINAL_EVENTS,
    CANARY_MIN_PREPARE_ARM_WINDOW_SECONDS,
    CANARY_POLICY_VERSION,
    CANARY_TARGET_NOTIONAL_USD,
    canary_policy,
    _ensure_initial_reconciliation,
    _latest_clean_account_snapshot,
    _retryable_risk_reasons,
)
from bp_engine.execution.fast_live import payload_sha256
from bp_engine.execution.fast_live_result import fast_live_account_snapshot
from bp_engine.execution.live import (
    InterlockDecision,
    _decimal,
    _decimal_or_negative_one,
    _official_zero_fill_reconciled_intents,
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
    ExecutionOrderRequest,
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


FAST_LIVE_MAX_POLYMARKET_SOURCE_AGE_SECONDS = Decimal("2")
FAST_LIVE_MAX_POLYMARKET_SOURCE_FUTURE_SKEW_SECONDS = Decimal("1")


@dataclass(frozen=True)
class PolymarketSourceTimeHealth:
    eligible: bool
    reason: str | None
    received_at: datetime | None
    source_timestamp: datetime | None
    source_age_seconds: Decimal | None
    transport_lag_seconds: Decimal | None


def continuous_fast_live_policy(*, max_consecutive_losses: int):
    return replace(
        canary_policy(),
        cooldown_seconds=Decimal("0"),
        max_consecutive_losses=max_consecutive_losses,
    )


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


def _polymarket_source_time_health(
    connection,
    *,
    condition_id: str,
    token_id: str,
    observed_at: datetime,
) -> PolymarketSourceTimeHealth:
    observed = _stored_utc(observed_at, "observed_at")
    rows = connection.execute(
        select(
            schema.raw_market_events.c.received_at,
            schema.raw_market_events.c.source_timestamp,
            schema.raw_market_events.c.payload,
        )
        .where(
            schema.raw_market_events.c.source == "polymarket",
            schema.raw_market_events.c.stream == "market",
            schema.raw_market_events.c.instrument == condition_id,
            schema.raw_market_events.c.event_type == "price_change",
            schema.raw_market_events.c.source_timestamp.is_not(None),
            schema.raw_market_events.c.received_at <= observed,
        )
        .order_by(
            schema.raw_market_events.c.received_at.desc(),
            schema.raw_market_events.c.id.desc(),
        )
        .limit(128)
    ).mappings().all()

    row = None
    for candidate in rows:
        payload = candidate["payload"]
        if not isinstance(payload, Mapping):
            continue
        changes = payload.get("price_changes")
        if not isinstance(changes, list):
            continue
        if any(
            isinstance(change, Mapping)
            and str(change.get("asset_id") or "") == token_id
            for change in changes
        ):
            row = candidate
            break

    if row is None:
        return PolymarketSourceTimeHealth(
            eligible=False,
            reason="polymarket_source_time_unavailable",
            received_at=None,
            source_timestamp=None,
            source_age_seconds=None,
            transport_lag_seconds=None,
        )

    received_at = _stored_utc(row["received_at"], "polymarket.received_at")
    source_timestamp = _stored_utc(
        row["source_timestamp"],
        "polymarket.source_timestamp",
    )
    source_age_seconds = Decimal(
        str((observed - source_timestamp).total_seconds())
    )
    transport_lag_seconds = Decimal(
        str((received_at - source_timestamp).total_seconds())
    )

    if (
        source_age_seconds
        < -FAST_LIVE_MAX_POLYMARKET_SOURCE_FUTURE_SKEW_SECONDS
    ):
        reason = "polymarket_source_clock_ahead"
    elif source_age_seconds > FAST_LIVE_MAX_POLYMARKET_SOURCE_AGE_SECONDS:
        reason = "polymarket_source_lag"
    else:
        reason = None

    return PolymarketSourceTimeHealth(
        eligible=reason is None,
        reason=reason,
        received_at=received_at,
        source_timestamp=source_timestamp,
        source_age_seconds=source_age_seconds,
        transport_lag_seconds=transport_lag_seconds,
    )


def _polymarket_source_time_evidence(
    health: PolymarketSourceTimeHealth,
) -> dict[str, object]:
    return {
        "eligible": health.eligible,
        "reason": health.reason,
        "max_source_age_seconds": str(
            FAST_LIVE_MAX_POLYMARKET_SOURCE_AGE_SECONDS
        ),
        "max_future_skew_seconds": str(
            FAST_LIVE_MAX_POLYMARKET_SOURCE_FUTURE_SKEW_SECONDS
        ),
        "received_at": (
            health.received_at.isoformat()
            if health.received_at is not None
            else None
        ),
        "source_timestamp": (
            health.source_timestamp.isoformat()
            if health.source_timestamp is not None
            else None
        ),
        "source_age_seconds": (
            str(health.source_age_seconds)
            if health.source_age_seconds is not None
            else None
        ),
        "transport_lag_seconds": (
            str(health.transport_lag_seconds)
            if health.transport_lag_seconds is not None
            else None
        ),
    }


_FAST_LIVE_SOURCE_TIME_RETRYABLE_REASONS = frozenset(
    {
        "polymarket_source_time_unavailable",
        "polymarket_source_lag",
        "polymarket_source_clock_ahead",
    }
)


def _fast_live_retryable_risk_reasons(reasons: object) -> bool:
    if not isinstance(reasons, (list, tuple)):
        return False
    normalized = tuple(
        str(reason).strip()
        for reason in reasons
        if str(reason).strip()
    )
    if "live_interlock_blocked" in normalized:
        specific = tuple(
            reason
            for reason in normalized
            if reason != "live_interlock_blocked"
        )
        if not specific:
            return False
        normalized = specific

    return bool(normalized) and all(
        reason in _FAST_LIVE_SOURCE_TIME_RETRYABLE_REASONS
        or _retryable_risk_reasons((reason,))
        for reason in normalized
    )

@dataclass
class FrozenPaperCashTracker:
    current_cash: Decimal | None = None
    last_fill_id: int = 0
    last_settlement_id: int = 0
    last_refresh_query_ms: float = 0.0
    last_refresh_was_incremental: bool = False
    last_fill_cost_delta: Decimal = Decimal("0")
    last_settlement_payout_delta: Decimal = Decimal("0")

    def refresh(self, connection) -> Decimal:
        config = frozen_v3_paper_config()
        fill_state = (
            select(
                func.coalesce(
                    func.sum(schema.paper_fills.c.total_cost),
                    0,
                ).label("fill_cost_delta"),
                func.coalesce(
                    func.max(schema.paper_fills.c.id),
                    self.last_fill_id,
                ).label("max_fill_id"),
            )
            .select_from(
                schema.paper_fills.join(
                    schema.paper_orders,
                    schema.paper_fills.c.paper_order_id
                    == schema.paper_orders.c.paper_order_id,
                )
            )
            .where(
                schema.paper_orders.c.execution_version
                == V3_FROZEN_PAPER_EXECUTION_VERSION,
                schema.paper_fills.c.id > self.last_fill_id,
            )
            .subquery()
        )
        settlement_state = (
            select(
                func.coalesce(
                    func.sum(schema.paper_settlements.c.payout),
                    0,
                ).label("settlement_payout_delta"),
                func.coalesce(
                    func.max(schema.paper_settlements.c.id),
                    self.last_settlement_id,
                ).label("max_settlement_id"),
            )
            .select_from(
                schema.paper_settlements.join(
                    schema.paper_orders,
                    schema.paper_settlements.c.paper_order_id
                    == schema.paper_orders.c.paper_order_id,
                )
            )
            .where(
                schema.paper_orders.c.execution_version
                == V3_FROZEN_PAPER_EXECUTION_VERSION,
                schema.paper_settlements.c.id > self.last_settlement_id,
            )
            .subquery()
        )
        refresh_started_ns = time.monotonic_ns()
        row = connection.execute(
            select(
                fill_state.c.fill_cost_delta,
                fill_state.c.max_fill_id,
                settlement_state.c.settlement_payout_delta,
                settlement_state.c.max_settlement_id,
            ).select_from(fill_state.join(settlement_state, true()))
        ).mappings().one()
        refresh_completed_ns = time.monotonic_ns()

        fill_delta = _decimal(
            row["fill_cost_delta"],
            "paper_fill_cost_delta",
        )
        payout_delta = _decimal(
            row["settlement_payout_delta"],
            "paper_settlement_payout_delta",
        )
        was_incremental = self.current_cash is not None
        starting_cash = (
            self.current_cash
            if self.current_cash is not None
            else config.starting_cash_usd
        )
        current_cash = derive_paper_cash(
            starting_cash=starting_cash,
            fill_costs=(fill_delta,),
            settlement_payouts=(payout_delta,),
        )
        self.current_cash = current_cash
        self.last_fill_id = int(row["max_fill_id"])
        self.last_settlement_id = int(row["max_settlement_id"])
        self.last_refresh_query_ms = (
            refresh_completed_ns - refresh_started_ns
        ) / 1_000_000
        self.last_refresh_was_incremental = was_incremental
        self.last_fill_cost_delta = fill_delta
        self.last_settlement_payout_delta = payout_delta
        return current_cash


def _current_frozen_paper_cash(
    connection,
    *,
    tracker: FrozenPaperCashTracker | None = None,
) -> Decimal:
    active_tracker = tracker or FrozenPaperCashTracker()
    return active_tracker.refresh(connection)


def _has_preview_arm_window(
    prediction: dict[str, object],
    *,
    observed_at: datetime,
) -> bool:
    market_end_at = _stored_utc(
        prediction["market_end_at"],
        "market_end_at",
    )
    remaining = Decimal(
        str((market_end_at - observed_at).total_seconds())
    )
    return remaining >= CANARY_MIN_PREPARE_ARM_WINDOW_SECONDS


def _session_evaluated_prediction_ids(
    connection,
    *,
    activated_at: datetime,
) -> set[str]:
    rows = connection.execute(
        select(
            schema.live_risk_decisions.c.prediction_id,
            schema.live_risk_decisions.c.eligible,
            schema.live_risk_decisions.c.reasons,
        ).where(
            schema.live_risk_decisions.c.policy_version
            == CANARY_POLICY_VERSION,
            schema.live_risk_decisions.c.created_at >= activated_at,
        )
    ).mappings()
    terminal: set[str] = set()
    for row in rows:
        prediction_id = str(row["prediction_id"])
        if row["eligible"] is True or not _retryable_risk_reasons(
            row["reasons"]
        ):
            terminal.add(prediction_id)
    return terminal


def _prediction_candidate(
    connection,
    *,
    activated_at: datetime,
    preview_observed_at: datetime | None = None,
) -> dict[str, object] | None:
    evaluated = _session_evaluated_prediction_ids(
        connection,
        activated_at=activated_at,
    )
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
        if prediction_id in evaluated:
            continue
        candidate = dict(row)
        if (
            preview_observed_at is not None
            and not _has_preview_arm_window(
                candidate,
                observed_at=preview_observed_at,
            )
        ):
            continue
        return candidate
    return None


class FastLiveDraftUnavailable(RuntimeError):
    def __init__(self, status: str, reason: str) -> None:
        super().__init__(
            f"frozen V3 prediction cannot build live-equivalent order: {reason}"
        )
        self.status = status
        self.reason = reason


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
        raise FastLiveDraftUnavailable(
            draft.status,
            draft.reason,
        )
    paper_order_id = canonical_hash(
        {
            "prediction_id": draft.request.prediction_id,
            "execution_version": draft.request.execution_version,
        }
    )
    return paper_order_id, draft





def _unresolved_pending_intent_id(
    connection,
    *,
    observed_at: datetime,
) -> str | None:
    latest_terminal_event = (
        select(schema.live_order_events.c.event_type)
        .where(
            schema.live_order_events.c.intent_id
            == schema.live_order_intents.c.intent_id,
            schema.live_order_events.c.event_type.in_(
                CANARY_INTENT_TERMINAL_EVENTS
            ),
        )
        .order_by(schema.live_order_events.c.id.desc())
        .limit(1)
        .correlate(schema.live_order_intents)
        .scalar_subquery()
    )
    pending = connection.execute(
        select(
            schema.live_order_intents.c.intent_id,
            latest_terminal_event.label("terminal_event"),
        )
        .where(
            schema.live_order_intents.c.policy_version
            == CANARY_POLICY_VERSION
        )
        .order_by(schema.live_order_intents.c.id.desc())
        .limit(1)
    ).mappings().one_or_none()
    if pending is None:
        return None
    if pending["terminal_event"] is not None:
        return None

    intent_id = str(pending["intent_id"])
    if intent_id in _official_zero_fill_reconciled_intents(
        connection,
        observed_at=observed_at,
    ):
        return None
    return intent_id


def ensure_fast_live_initial_reconciliation(
    *,
    engine: Engine,
    observed_at: datetime,
    official_open_order_count: int,
    collateral_balance_usd: Decimal,
) -> None:
    repository = LiveReadinessRepository()
    with engine.begin() as connection:
        _ensure_initial_reconciliation(
            connection,
            repository=repository,
            observed_at=observed_at,
            official_open_order_count=official_open_order_count,
            collateral_balance_usd=collateral_balance_usd,
        )


def preview_fast_live_candidate(
    *,
    engine: Engine,
    activated_at: datetime,
    observed_at: datetime,
    max_consecutive_losses: int,
    paper_cash_tracker: FrozenPaperCashTracker | None = None,
) -> dict[str, object]:
    policy = continuous_fast_live_policy(
        max_consecutive_losses=max_consecutive_losses,
    )
    preview_started_ns = time.monotonic_ns()

    with engine.begin() as connection:
        pending_started_ns = time.monotonic_ns()
        pending_intent_id = _unresolved_pending_intent_id(
            connection,
            observed_at=observed_at,
        )
        pending_completed_ns = time.monotonic_ns()
        if pending_intent_id is not None:
            return {
                "status": "blocked",
                "reason": "pending_live_intent_requires_reconciliation",
                "intent_id": pending_intent_id,
            }

        prediction_started_ns = time.monotonic_ns()
        prediction = _prediction_candidate(
            connection,
            activated_at=activated_at,
            preview_observed_at=observed_at,
        )
        prediction_completed_ns = time.monotonic_ns()
        if prediction is None:
            return {
                "status": "waiting",
                "reason": "no_new_frozen_v3_trade_prediction",
            }

        paper_cash_started_ns = time.monotonic_ns()
        available_paper_cash = _current_frozen_paper_cash(
            connection,
            tracker=paper_cash_tracker,
        )
        paper_cash_completed_ns = time.monotonic_ns()
        try:
            paper_order_id, draft = build_fast_live_draft(
                prediction,
                available_paper_cash=available_paper_cash,
            )
        except FastLiveDraftUnavailable as exc:
            return {
                "status": "blocked",
                "reason": "frozen_paper_order_unavailable",
                "paper_status": exc.status,
                "paper_reason": exc.reason,
                "prediction_id": str(prediction["prediction_id"]),
                "retryable": exc.status == "INSUFFICIENT_PAPER_CASH",
            }
        request = draft.request
        if request.target_notional_usd != CANARY_TARGET_NOTIONAL_USD:
            raise RuntimeError("frozen paper order target changed")
        if request.target_notional_usd > policy.max_trade_size_usd:
            raise RuntimeError("frozen paper order exceeds live ceiling")
        if not _source_request_matches(prediction, request):
            raise RuntimeError(
                "frozen paper order no longer matches source prediction"
            )

        source_time_health = _polymarket_source_time_health(
            connection,
            condition_id=request.condition_id,
            token_id=request.token_id,
            observed_at=observed_at,
        )
        if not source_time_health.eligible:
            return {
                "status": "blocked",
                "reason": source_time_health.reason,
                "reasons": (source_time_health.reason,),
                "retryable": True,
                "prediction_id": request.prediction_id,
                "paper_order_id": paper_order_id,
                "polymarket_source_time": (
                    _polymarket_source_time_evidence(source_time_health)
                ),
            }

        market_end_at = _stored_utc(
            prediction["market_end_at"],
            "market_end_at",
        )
        arm_window = Decimal(
            str((market_end_at - observed_at).total_seconds())
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

        request_id = derive_id(
            "live-request",
            semantic_sha256(request.as_mapping(raw=True)),
        )
        candidate_id = derive_id(
            "fast-live-candidate",
            semantic_sha256(
                {
                    "prediction_id": request.prediction_id,
                    "paper_order_id": paper_order_id,
                    "request": request.as_mapping(raw=True),
                    "preview_observed_at": observed_at.isoformat(),
                }
            ),
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
        "paper_cash_query_ms": (
            paper_cash_tracker.last_refresh_query_ms
            if paper_cash_tracker is not None
            else None
        ),
        "paper_cash_incremental": (
            paper_cash_tracker.last_refresh_was_incremental
            if paper_cash_tracker is not None
            else False
        ),
        "preview_pending_gate_ms": (
            pending_completed_ns - pending_started_ns
        ) / 1_000_000,
        "preview_prediction_candidate_ms": (
            prediction_completed_ns - prediction_started_ns
        ) / 1_000_000,
        "preview_paper_cash_ms": (
            paper_cash_completed_ns - paper_cash_started_ns
        ) / 1_000_000,
        "preview_build_ms": (
            time.monotonic_ns() - preview_started_ns
        ) / 1_000_000,
    }
    return {
        "status": "prepared",
        "action": "submit",
        "intent_id": candidate_id,
        "request_id": request_id,
        "risk_decision_id": f"risk-pending:{candidate_id}",
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
        "risk_status": "pending",
    }


def _request_from_preview(
    preview: Mapping[str, object],
) -> tuple[str, ExecutionOrderRequest]:
    if str(preview.get("status") or "") != "prepared":
        raise RuntimeError("fast live preview is not prepared")
    if str(preview.get("risk_status") or "") != "pending":
        raise RuntimeError("fast live preview risk status changed")
    raw = preview.get("request")
    if not isinstance(raw, Mapping):
        raise RuntimeError("fast live preview request missing")
    try:
        request = ExecutionOrderRequest(
            prediction_id=str(raw["prediction_id"]),
            prediction_semantic_sha256=str(
                raw["prediction_semantic_sha256"]
            ),
            condition_id=str(raw["condition_id"]),
            token_id=str(raw["token_id"]),
            selected_side=str(raw["selected_side"]),
            action=str(raw["action"]),
            requested_shares=Decimal(str(raw["requested_shares"])),
            target_notional_usd=Decimal(
                str(raw["target_notional_usd"])
            ),
            submitted_at=datetime.fromisoformat(
                str(raw["submitted_at"])
            ),
            arrival_at=datetime.fromisoformat(
                str(raw["arrival_at"])
            ),
            expires_at=datetime.fromisoformat(
                str(raw["expires_at"])
            ),
            limit_price=Decimal(str(raw["limit_price"])),
            execution_version=str(raw["execution_version"]),
            execution_config_sha256=str(
                raw["execution_config_sha256"]
            ),
        )
    except (KeyError, ValueError, ArithmeticError) as exc:
        raise RuntimeError("fast live preview request invalid") from exc

    if str(preview.get("prediction_id") or "") != request.prediction_id:
        raise RuntimeError("fast live preview prediction binding changed")
    paper_order_id = canonical_hash(
        {
            "prediction_id": request.prediction_id,
            "execution_version": request.execution_version,
        }
    )
    if str(preview.get("paper_order_id") or "") != paper_order_id:
        raise RuntimeError("fast live preview paper order binding changed")
    request_id = derive_id(
        "live-request",
        semantic_sha256(request.as_mapping(raw=True)),
    )
    if str(preview.get("request_id") or "") != request_id:
        raise RuntimeError("fast live preview request id changed")
    return paper_order_id, request


def prepare_fast_live_candidate(
    *,
    engine: Engine,
    activated_at: datetime,
    observed_at: datetime,
    preview: Mapping[str, object] | None = None,
    interlock: InterlockDecision,
    api_healthy: bool,
    official_open_order_count: int,
    collateral_balance_usd: Decimal,
    paper_cash_tracker: FrozenPaperCashTracker | None = None,
    initial_reconciliation_verified: bool = False,
    max_consecutive_losses: int,
) -> dict[str, object]:
    repository = LiveReadinessRepository()
    policy = continuous_fast_live_policy(
        max_consecutive_losses=max_consecutive_losses,
    )

    with engine.begin() as connection:
        if not initial_reconciliation_verified:
            _ensure_initial_reconciliation(
                connection,
                repository=repository,
                observed_at=observed_at,
                official_open_order_count=official_open_order_count,
                collateral_balance_usd=collateral_balance_usd,
            )

        pending_intent_id = _unresolved_pending_intent_id(
            connection,
            observed_at=observed_at,
        )
        if pending_intent_id is not None:
            return {
                "status": "blocked",
                "reason": "pending_live_intent_requires_reconciliation",
                "intent_id": pending_intent_id,
            }

        if preview is None:
            prediction = _prediction_candidate(
                connection,
                activated_at=activated_at,
            )
            if prediction is None:
                return {
                    "status": "waiting",
                    "reason": "no_new_frozen_v3_trade_prediction",
                }

            available_paper_cash = _current_frozen_paper_cash(
                connection,
                tracker=paper_cash_tracker,
            )
            try:
                paper_order_id, draft = build_fast_live_draft(
                    prediction,
                    available_paper_cash=available_paper_cash,
                )
            except FastLiveDraftUnavailable as exc:
                return {
                    "status": "blocked",
                    "reason": "frozen_paper_order_unavailable",
                    "paper_status": exc.status,
                    "paper_reason": exc.reason,
                    "prediction_id": str(prediction["prediction_id"]),
                    "retryable": exc.status == "INSUFFICIENT_PAPER_CASH",
                }
            request = draft.request
        else:
            paper_order_id, request = _request_from_preview(preview)
            prediction = connection.execute(
                select(schema.live_predictions).where(
                    schema.live_predictions.c.prediction_id
                    == request.prediction_id,
                    schema.live_predictions.c.prediction_version
                    == "v3-frozen-paper-v1",
                    schema.live_predictions.c.trade.is_(True),
                    schema.live_predictions.c.executable.is_(True),
                    schema.live_predictions.c.recorded_at >= activated_at,
                )
            ).mappings().one_or_none()
            if prediction is None:
                raise RuntimeError(
                    "frozen preview prediction is no longer available"
                )
            prediction = dict(prediction)
            preview_market_end = datetime.fromisoformat(
                str(preview.get("market_end_at") or "")
            )
            if _stored_utc(
                preview_market_end,
                "preview.market_end_at",
            ) != _stored_utc(
                prediction["market_end_at"],
                "market_end_at",
            ):
                raise RuntimeError("fast live preview market end changed")

        if request.target_notional_usd != CANARY_TARGET_NOTIONAL_USD:
            raise RuntimeError("frozen paper order target changed")
        if request.target_notional_usd > policy.max_trade_size_usd:
            raise RuntimeError("frozen paper order exceeds live ceiling")
        if not _source_request_matches(prediction, request):
            raise RuntimeError(
                "frozen paper order no longer matches source prediction"
            )

        account = fast_live_account_snapshot(
            connection,
            observed_at=observed_at,
        )
        selected_liquidity = _selected_liquidity_usd(
            connection,
            prediction=prediction,
            request=request,
            observed_at=observed_at,
            freshness_seconds=policy.max_prediction_age_seconds,
        )
        source_time_health = _polymarket_source_time_health(
            connection,
            condition_id=request.condition_id,
            token_id=request.token_id,
            observed_at=observed_at,
        )
        effective_interlock_reasons = list(interlock.reasons)
        if source_time_health.reason is not None:
            effective_interlock_reasons.append(source_time_health.reason)
        effective_interlock = InterlockDecision(
            eligible=interlock.eligible and source_time_health.eligible,
            reasons=tuple(effective_interlock_reasons),
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
            interlock_eligible=effective_interlock.eligible,
            interlock_reasons=effective_interlock.reasons,
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
                "interlock_eligible": effective_interlock.eligible,
                "interlock_reasons": effective_interlock.reasons,
                "polymarket_source_time": (
                    _polymarket_source_time_evidence(source_time_health)
                ),
                "fast_live": True,
                "finalized_from_preview": preview is not None,
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
                "retryable": _fast_live_retryable_risk_reasons(
                    decision.reasons
                ),
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

        edge_config = prediction.get("edge_config")
        if not isinstance(edge_config, dict):
            raise RuntimeError("frozen prediction edge config missing")
        modeled_fee_rate = Decimal(str(edge_config.get("fee_rate")))
        if not Decimal("0") <= modeled_fee_rate <= Decimal("1"):
            raise RuntimeError("frozen prediction fee rate invalid")

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
                "modeled_fee_rate": str(modeled_fee_rate),
                "fast_live": True,
                "finalized_from_preview": preview is not None,
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
        "finalized_from_preview": preview is not None,
        "prepare_after_paper_seconds": str(
            (observed_at - paper_order_submitted_at).total_seconds()
        ),
        "paper_cash_query_ms": (
            paper_cash_tracker.last_refresh_query_ms
            if paper_cash_tracker is not None and preview is None
            else None
        ),
        "paper_cash_incremental": (
            paper_cash_tracker.last_refresh_was_incremental
            if paper_cash_tracker is not None and preview is None
            else False
        ),
    }
    return {
        "status": "prepared",
        "action": "submit",
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

