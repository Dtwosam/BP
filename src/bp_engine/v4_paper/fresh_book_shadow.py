from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any

from bp_engine.features.hashing import canonical_hash
from bp_engine.v3_research.policy import V3ExecutionBook, edge_decision_v3
from bp_engine.v4_paper.inference import (
    FROZEN_V4_FEE_RATE,
    FROZEN_V4_MIN_EDGE,
    FROZEN_V4_MODEL_SHA256,
    FROZEN_V4_SLIPPAGE_BUFFER,
)
from bp_engine.v4_paper.source_time_features import V4_SOURCE_TIME_FEATURE_VERSION

V4_FRESH_BOOK_SHADOW_VERSION = "paper-execution-v4-source-time-fresh-book-shadow-v2"
EXTREME_EDGE_OBSERVATION_THRESHOLD = Decimal("0.50")
TARGET_NOTIONAL_USD = Decimal("5.00")
SHARE_PRECISION = 6
_ZERO = Decimal("0")
_ONE = Decimal("1")


class V4FreshBookShadowError(RuntimeError):
    """Raised when a V4 fresh-book shadow input violates the frozen contract."""


@dataclass(frozen=True)
class V4FreshBookShadowResult:
    shadow_version: str
    prediction_id: str
    model_sha256: str
    source_feature_version: str
    decision_at: datetime
    prediction_recorded_at: datetime
    quote_observed_at: datetime
    condition_id: str
    token_id: str
    selected_side: str
    probability_up: Decimal
    side_probability: Decimal
    best_ask: Decimal | None
    limit_price: Decimal | None
    raw_edge: Decimal | None
    fee_per_share_at_signal: Decimal | None
    cost_adjusted_edge: Decimal | None
    extreme_edge_observation: bool
    decision_reason: str
    trade: bool
    requested_shares: Decimal
    filled_shares: Decimal
    gross_fill_cost: Decimal
    total_fees: Decimal
    total_fill_cost: Decimal
    full_fill: bool
    displayed_ask_levels: tuple[tuple[Decimal, Decimal], ...]
    semantic_sha256: str

    def as_mapping(self) -> dict[str, Any]:
        values = asdict(self)
        for key in (
            "decision_at",
            "prediction_recorded_at",
            "quote_observed_at",
        ):
            values[key] = values[key].isoformat()
        values["displayed_ask_levels"] = [
            [str(price), str(size)] for price, size in self.displayed_ask_levels
        ]
        for key in (
            "probability_up",
            "side_probability",
            "best_ask",
            "limit_price",
            "raw_edge",
            "fee_per_share_at_signal",
            "cost_adjusted_edge",
            "requested_shares",
            "filled_shares",
            "gross_fill_cost",
            "total_fees",
            "total_fill_cost",
        ):
            value = values[key]
            values[key] = None if value is None else str(value)
        return values


def _utc(value: object, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise V4FreshBookShadowError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise V4FreshBookShadowError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _decimal(value: object, name: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise V4FreshBookShadowError(f"{name} must be numeric")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise V4FreshBookShadowError(f"{name} must be numeric") from exc
    if not result.is_finite():
        raise V4FreshBookShadowError(f"{name} must be finite")
    return result


def _levels(
    values: Sequence[tuple[object, object]],
) -> tuple[tuple[Decimal, Decimal], ...]:
    merged: dict[Decimal, Decimal] = {}
    for raw_price, raw_size in values:
        price = _decimal(raw_price, "ask price")
        size = _decimal(raw_size, "ask size")
        if not _ZERO < price <= _ONE:
            raise V4FreshBookShadowError("ask price must be within (0, 1]")
        if size < _ZERO:
            raise V4FreshBookShadowError("ask size must be non-negative")
        if size == _ZERO:
            continue
        merged[price] = merged.get(price, _ZERO) + size
    return tuple(sorted(merged.items()))


def _result_hash(values: Mapping[str, Any]) -> str:
    semantic = dict(values)
    semantic.pop("semantic_sha256", None)
    return canonical_hash(semantic)


def evaluate_v4_fresh_book_shadow(
    prediction: Mapping[str, Any],
    ask_levels: Sequence[tuple[object, object]],
    *,
    quote_observed_at: datetime,
) -> V4FreshBookShadowResult:
    model_sha256 = str(prediction.get("model_sha256") or "")
    if model_sha256 != FROZEN_V4_MODEL_SHA256:
        raise V4FreshBookShadowError("unsupported V4 model artifact")

    source_feature_version = str(prediction.get("source_feature_version") or "")
    if source_feature_version != V4_SOURCE_TIME_FEATURE_VERSION:
        raise V4FreshBookShadowError("unsupported V4 source feature version")

    prediction_id = str(prediction.get("prediction_id") or "")
    condition_id = str(prediction.get("condition_id") or "")
    if not prediction_id or not condition_id:
        raise V4FreshBookShadowError("prediction identity missing")

    decision_at = _utc(prediction.get("decision_at"), "decision_at")
    recorded_at = _utc(prediction.get("recorded_at"), "recorded_at")
    observed_at = _utc(quote_observed_at, "quote_observed_at")
    if recorded_at < decision_at:
        raise V4FreshBookShadowError("prediction cannot predate decision")
    if observed_at < recorded_at:
        raise V4FreshBookShadowError("fresh quote cannot predate prediction")

    probability_up = _decimal(prediction.get("probability_up"), "probability_up")
    if probability_up < _ZERO or probability_up > _ONE:
        raise V4FreshBookShadowError("probability_up must be within [0, 1]")

    selected_side = "up" if probability_up >= Decimal("0.5") else "down"
    token_key = "up_token_id" if selected_side == "up" else "down_token_id"
    token_id = str(prediction.get(token_key) or "")
    if not token_id:
        raise V4FreshBookShadowError(f"{token_key} missing")

    levels = _levels(ask_levels)
    best_ask = levels[0][0] if levels else None
    book = V3ExecutionBook(
        up_best_bid=None,
        up_best_ask=float(best_ask)
        if selected_side == "up" and best_ask is not None
        else None,
        up_fresh=selected_side == "up" and best_ask is not None,
        down_best_bid=None,
        down_best_ask=float(best_ask)
        if selected_side == "down" and best_ask is not None
        else None,
        down_fresh=selected_side == "down" and best_ask is not None,
    )
    decision = edge_decision_v3(
        calibrated_probability_up=float(probability_up),
        book=book,
        fee_rate=float(FROZEN_V4_FEE_RATE),
        slippage_buffer=float(FROZEN_V4_SLIPPAGE_BUFFER),
        min_edge=float(FROZEN_V4_MIN_EDGE),
    )

    requested_shares = _ZERO
    filled_shares = _ZERO
    gross_fill_cost = _ZERO
    total_fees = _ZERO
    limit_price: Decimal | None = None

    if decision.trade:
        assert best_ask is not None
        limit_price = min(_ONE, best_ask + FROZEN_V4_SLIPPAGE_BUFFER)
        worst_fee_per_share = (
            FROZEN_V4_FEE_RATE * limit_price * (_ONE - limit_price)
        )
        worst_total_per_share = limit_price + worst_fee_per_share
        quantum = _ONE.scaleb(-SHARE_PRECISION)
        requested_shares = (TARGET_NOTIONAL_USD / worst_total_per_share).quantize(
            quantum,
            rounding=ROUND_DOWN,
        )
        remaining = requested_shares
        for price, size in levels:
            if remaining <= _ZERO or price > limit_price:
                break
            shares = min(size, remaining)
            filled_shares += shares
            gross_fill_cost += shares * price
            total_fees += (
                shares * FROZEN_V4_FEE_RATE * price * (_ONE - price)
            )
            remaining -= shares

    side_probability = (
        probability_up if selected_side == "up" else _ONE - probability_up
    )
    raw_edge = None if decision.raw_edge is None else Decimal(str(decision.raw_edge))
    fee_signal = (
        None
        if best_ask is None
        else FROZEN_V4_FEE_RATE * best_ask * (_ONE - best_ask)
    )
    adjusted = (
        None
        if decision.cost_adjusted_edge is None
        else Decimal(str(decision.cost_adjusted_edge))
    )
    total_fill_cost = gross_fill_cost + total_fees
    full_fill = requested_shares > _ZERO and filled_shares == requested_shares
    extreme_edge_observation = (
        adjusted is not None and adjusted > EXTREME_EDGE_OBSERVATION_THRESHOLD
    )

    semantic_values = {
        "shadow_version": V4_FRESH_BOOK_SHADOW_VERSION,
        "prediction_id": prediction_id,
        "model_sha256": model_sha256,
        "source_feature_version": source_feature_version,
        "decision_at": decision_at.isoformat(),
        "prediction_recorded_at": recorded_at.isoformat(),
        "quote_observed_at": observed_at.isoformat(),
        "condition_id": condition_id,
        "token_id": token_id,
        "selected_side": selected_side,
        "probability_up": str(probability_up),
        "side_probability": str(side_probability),
        "best_ask": None if best_ask is None else str(best_ask),
        "limit_price": None if limit_price is None else str(limit_price),
        "raw_edge": None if raw_edge is None else str(raw_edge),
        "fee_per_share_at_signal": None if fee_signal is None else str(fee_signal),
        "cost_adjusted_edge": None if adjusted is None else str(adjusted),
        "extreme_edge_observation": extreme_edge_observation,
        "decision_reason": decision.reason,
        "trade": decision.trade,
        "requested_shares": str(requested_shares),
        "filled_shares": str(filled_shares),
        "gross_fill_cost": str(gross_fill_cost),
        "total_fees": str(total_fees),
        "total_fill_cost": str(total_fill_cost),
        "full_fill": full_fill,
        "displayed_ask_levels": [[str(p), str(q)] for p, q in levels],
    }
    digest = _result_hash(semantic_values)
    return V4FreshBookShadowResult(
        shadow_version=V4_FRESH_BOOK_SHADOW_VERSION,
        prediction_id=prediction_id,
        model_sha256=model_sha256,
        source_feature_version=source_feature_version,
        decision_at=decision_at,
        prediction_recorded_at=recorded_at,
        quote_observed_at=observed_at,
        condition_id=condition_id,
        token_id=token_id,
        selected_side=selected_side,
        probability_up=probability_up,
        side_probability=side_probability,
        best_ask=best_ask,
        limit_price=limit_price,
        raw_edge=raw_edge,
        fee_per_share_at_signal=fee_signal,
        cost_adjusted_edge=adjusted,
        extreme_edge_observation=extreme_edge_observation,
        decision_reason=decision.reason,
        trade=decision.trade,
        requested_shares=requested_shares,
        filled_shares=filled_shares,
        gross_fill_cost=gross_fill_cost,
        total_fees=total_fees,
        total_fill_cost=total_fill_cost,
        full_fill=full_fill,
        displayed_ask_levels=levels,
        semantic_sha256=digest,
    )


def settle_v4_fresh_book_shadow(
    result: V4FreshBookShadowResult,
    *,
    official_outcome: str,
) -> Decimal:
    outcome = str(official_outcome).lower()
    if outcome not in {"up", "down"}:
        raise V4FreshBookShadowError("official_outcome must be Up or Down")
    payout = result.filled_shares if outcome == result.selected_side else _ZERO
    return payout - result.total_fill_cost
