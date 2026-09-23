from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from bp_engine.execution.models import (
    ExecutionOrderRequest,
    PaperExecutionConfig,
    V3_FROZEN_PAPER_EXECUTION_VERSION,
    V3_FROZEN_PAPER_LATENCY_MS,
    V3_FROZEN_PAPER_ORDER_TTL_MS,
    V3_FROZEN_PAPER_SHARE_PRECISION,
    V3_FROZEN_PAPER_TARGET_NOTIONAL_USD,
    V3_LIVE_CANARY_EXECUTION_VERSION,
)
from bp_engine.execution.paper import PaperOrderDraft, build_paper_order
from bp_engine.features.hashing import canonical_hash
from bp_engine.v3_paper.service import (
    FROZEN_FEE_RATE,
    FROZEN_MAX_BOOK_AGE_SECONDS,
    FROZEN_MIN_EDGE,
    FROZEN_OFFSET_SECONDS,
    FROZEN_SLIPPAGE_BUFFER,
    V3_PAPER_PREDICTION_VERSION,
)

CANARY_POLICY_VERSION = "live-risk-v3-canary-v1"
CANARY_TARGET_NOTIONAL_USD = Decimal("5.00")
CANARY_MAX_TOTAL_EXPOSURE_USD = Decimal("5.00")
CANARY_MAX_DAILY_LOSS_USD = Decimal("5.00")
CANARY_MAX_CONSECUTIVE_LOSSES = 1
CANARY_MIN_LIQUIDITY_USD = Decimal("1.00")
CANARY_MAX_SPREAD = Decimal("0.05")
CANARY_MAX_PREDICTION_AGE_SECONDS = Decimal("10")
CANARY_MIN_TIME_TO_EXPIRY_SECONDS = Decimal("45")
CANARY_COOLDOWN_SECONDS = Decimal("300")


class V3LiveCanaryIntegrityError(RuntimeError):
    """Raised when a live canary input drifts from the frozen V3 contract."""


def _decimal(value: object, *, name: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (ValueError, TypeError) as exc:
        raise V3LiveCanaryIntegrityError(f"{name} must be numeric") from exc
    if not result.is_finite():
        raise V3LiveCanaryIntegrityError(f"{name} must be finite")
    return result


def _assert_frozen_prediction(prediction: Mapping[str, Any]) -> None:
    if prediction.get("prediction_version") != V3_PAPER_PREDICTION_VERSION:
        raise V3LiveCanaryIntegrityError("prediction version is not frozen V3")
    if prediction.get("source_feature_version") != "core-v3-btc-native":
        raise V3LiveCanaryIntegrityError("feature version is not frozen V3")
    if int(prediction.get("selected_offset_seconds", -1)) != FROZEN_OFFSET_SECONDS:
        raise V3LiveCanaryIntegrityError("selected offset changed")
    if _decimal(prediction.get("decision_min_edge"), name="decision_min_edge") != Decimal(
        str(FROZEN_MIN_EDGE)
    ):
        raise V3LiveCanaryIntegrityError("minimum edge changed")
    if _decimal(prediction.get("slippage_buffer"), name="slippage_buffer") != Decimal(
        str(FROZEN_SLIPPAGE_BUFFER)
    ):
        raise V3LiveCanaryIntegrityError("slippage buffer changed")
    edge_config = prediction.get("edge_config")
    if not isinstance(edge_config, Mapping):
        raise V3LiveCanaryIntegrityError("edge_config missing")
    if _decimal(edge_config.get("fee_rate"), name="fee_rate") != Decimal(
        str(FROZEN_FEE_RATE)
    ):
        raise V3LiveCanaryIntegrityError("fee rate changed")
    if int(edge_config.get("max_selected_book_age_seconds", -1)) != (
        FROZEN_MAX_BOOK_AGE_SECONDS
    ):
        raise V3LiveCanaryIntegrityError("selected-book freshness changed")


def build_v3_live_canary_request(
    prediction: Mapping[str, Any],
) -> ExecutionOrderRequest:
    """Build the live request from the same deterministic $5 frozen-V3 paper order."""
    _assert_frozen_prediction(prediction)
    paper_config = PaperExecutionConfig(
        starting_cash_usd=Decimal("100.00"),
        target_notional_usd=V3_FROZEN_PAPER_TARGET_NOTIONAL_USD,
        latency_ms=V3_FROZEN_PAPER_LATENCY_MS,
        order_ttl_ms=V3_FROZEN_PAPER_ORDER_TTL_MS,
        share_precision=V3_FROZEN_PAPER_SHARE_PRECISION,
        execution_version=V3_FROZEN_PAPER_EXECUTION_VERSION,
        prediction_version=V3_PAPER_PREDICTION_VERSION,
    )
    draft = build_paper_order(
        prediction,
        paper_config,
        available_cash=CANARY_TARGET_NOTIONAL_USD,
    )
    if not isinstance(draft, PaperOrderDraft):
        raise V3LiveCanaryIntegrityError(
            f"frozen V3 signal cannot create canary order: {draft.reason}"
        )
    source = draft.request
    canary_config = {
        "execution_version": V3_LIVE_CANARY_EXECUTION_VERSION,
        "source_execution_version": V3_FROZEN_PAPER_EXECUTION_VERSION,
        "prediction_version": V3_PAPER_PREDICTION_VERSION,
        "target_notional_usd": str(CANARY_TARGET_NOTIONAL_USD),
        "latency_ms": V3_FROZEN_PAPER_LATENCY_MS,
        "order_ttl_ms": V3_FROZEN_PAPER_ORDER_TTL_MS,
        "share_precision": V3_FROZEN_PAPER_SHARE_PRECISION,
        "single_external_attempt": True,
    }
    return ExecutionOrderRequest(
        prediction_id=source.prediction_id,
        prediction_semantic_sha256=source.prediction_semantic_sha256,
        condition_id=source.condition_id,
        token_id=source.token_id,
        selected_side=source.selected_side,
        action=source.action,
        requested_shares=source.requested_shares,
        target_notional_usd=CANARY_TARGET_NOTIONAL_USD,
        submitted_at=source.submitted_at,
        arrival_at=source.arrival_at,
        expires_at=source.expires_at,
        limit_price=source.limit_price,
        execution_version=V3_LIVE_CANARY_EXECUTION_VERSION,
        execution_config_sha256=canonical_hash(canary_config),
    )
