from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any

from bp_engine.execution.book import ReplayedBook
from bp_engine.execution.models import ExecutionOrderRequest, PaperExecutionConfig
from bp_engine.features.hashing import canonical_hash

_ZERO = Decimal("0")
_ONE = Decimal("1")


@dataclass(frozen=True)
class PaperOrderDraft:
    request: ExecutionOrderRequest
    signal_selected_ask: Decimal
    signal_fee_rate: Decimal
    signal_slippage_buffer: Decimal
    execution_config: Mapping[str, object]
    market_end_at: datetime | None = None


@dataclass(frozen=True)
class PaperTerminalDraft:
    status: str
    remaining_shares: Decimal
    event_at: datetime | None
    reason: str


@dataclass(frozen=True)
class PaperFillDraft:
    fill_at: datetime
    shares: Decimal
    price: Decimal
    gross_cost: Decimal
    fee: Decimal
    total_cost: Decimal
    signal_ask_slippage: Decimal
    book_anchor_event_id: int
    book_anchor_dedupe_key: str
    book_applied_event_ids: tuple[int, ...]
    book_applied_dedupe_keys: tuple[str, ...]
    replay_cutoff_at: datetime


@dataclass(frozen=True)
class PaperSimulationResult:
    fills: tuple[PaperFillDraft, ...]
    terminal: PaperTerminalDraft
    filled_shares: Decimal
    remaining_shares: Decimal
    total_cost: Decimal


class PaperSimulationError(RuntimeError):
    """Raised when paper execution inputs violate deterministic replay invariants."""


def _decimal(value: object, *, name: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{name} must be numeric")
    try:
        numeric = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not numeric.is_finite():
        raise ValueError(f"{name} must be finite")
    return numeric


def _utc(value: object, *, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _terminal(
    status: str,
    *,
    reason: str,
    event_at: datetime | None = None,
    remaining_shares: Decimal = _ZERO,
) -> PaperTerminalDraft:
    return PaperTerminalDraft(
        status=status,
        remaining_shares=remaining_shares,
        event_at=event_at,
        reason=reason,
    )


def _skip_reason(prediction: Mapping[str, Any]) -> str | None:
    if prediction.get("trade") is not True:
        return "trade=false"
    if prediction.get("executable") is not True:
        return "executable=false"
    side = str(prediction.get("selected_side") or "").lower()
    if side not in {"up", "down"}:
        return "selected_side must be up or down"
    if prediction.get("selected_ask") is None:
        return "selected_ask is required"
    edge_config = prediction.get("edge_config")
    if not isinstance(edge_config, Mapping) or edge_config.get("fee_rate") is None:
        return "edge_config fee_rate is required"
    return None


def _safe_recorded_at(prediction: Mapping[str, Any]) -> datetime | None:
    value = prediction.get("recorded_at")
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(UTC)


def build_paper_order(
    prediction: Mapping[str, Any],
    config: PaperExecutionConfig,
    available_cash: Decimal,
) -> PaperOrderDraft | PaperTerminalDraft:
    """Build one deterministic, cash-solvent paper BUY request from an immutable signal."""

    skip_reason = _skip_reason(prediction)
    if skip_reason is not None:
        return _terminal(
            "SKIPPED",
            reason=skip_reason,
            event_at=_safe_recorded_at(prediction),
        )

    try:
        selected_side = str(prediction["selected_side"]).lower()
        selected_ask = _decimal(prediction["selected_ask"], name="selected_ask")
        slippage_buffer = _decimal(prediction["slippage_buffer"], name="slippage_buffer")
        edge_config = prediction["edge_config"]
        if not isinstance(edge_config, Mapping):
            raise ValueError("edge_config must be a mapping")
        fee_rate = _decimal(edge_config["fee_rate"], name="fee_rate")
        cash = _decimal(available_cash, name="available_cash")
        submitted_at = _utc(prediction["recorded_at"], name="recorded_at")
        market_end_at = _utc(prediction["market_end_at"], name="market_end_at")
    except (KeyError, ValueError) as exc:
        return _terminal(
            "SKIPPED",
            reason=str(exc),
            event_at=_safe_recorded_at(prediction),
        )

    if not _ZERO < selected_ask <= _ONE:
        return _terminal(
            "SKIPPED",
            reason="selected_ask must be within (0, 1]",
            event_at=submitted_at,
        )
    if slippage_buffer < _ZERO:
        return _terminal(
            "SKIPPED",
            reason="slippage_buffer must be non-negative",
            event_at=submitted_at,
        )
    if not _ZERO <= fee_rate <= _ONE:
        return _terminal(
            "SKIPPED",
            reason="fee_rate must be within [0, 1]",
            event_at=submitted_at,
        )
    if cash <= _ZERO:
        return _terminal(
            "INSUFFICIENT_PAPER_CASH",
            reason="available paper cash is not positive",
            event_at=submitted_at,
        )

    token_key = "up_token_id" if selected_side == "up" else "down_token_id"
    token_id = str(prediction.get(token_key) or "")
    if not token_id:
        return _terminal(
            "SKIPPED",
            reason=f"{token_key} is required",
            event_at=submitted_at,
        )

    prediction_id = str(prediction.get("prediction_id") or "")
    semantic_sha256 = str(prediction.get("semantic_sha256") or "")
    condition_id = str(prediction.get("condition_id") or "")
    if not prediction_id or not semantic_sha256 or not condition_id:
        return _terminal(
            "SKIPPED",
            reason="prediction identity fields are required",
            event_at=submitted_at,
        )

    arrival_at = submitted_at + timedelta(milliseconds=config.latency_ms)
    if market_end_at <= arrival_at:
        return _terminal(
            "MARKET_ENDED_UNFILLED",
            reason="market ended before simulated order arrival",
            event_at=market_end_at,
        )
    ttl_expiry = arrival_at + timedelta(milliseconds=config.order_ttl_ms)
    expires_at = min(ttl_expiry, market_end_at)

    limit_price = min(_ONE, selected_ask + slippage_buffer)
    worst_fee_per_share = fee_rate * limit_price * (_ONE - limit_price)
    worst_total_per_share = limit_price + worst_fee_per_share
    budget = min(config.target_notional_usd, cash)
    quantum = _ONE.scaleb(-config.share_precision)
    requested_shares = (budget / worst_total_per_share).quantize(
        quantum,
        rounding=ROUND_DOWN,
    )
    if requested_shares <= _ZERO:
        return _terminal(
            "INSUFFICIENT_PAPER_CASH",
            reason="paper cash cannot buy the minimum share precision",
            event_at=submitted_at,
        )

    execution_config = config.as_mapping()
    request = ExecutionOrderRequest(
        prediction_id=prediction_id,
        prediction_semantic_sha256=semantic_sha256,
        condition_id=condition_id,
        token_id=token_id,
        selected_side=selected_side,
        action="BUY",
        requested_shares=requested_shares,
        target_notional_usd=config.target_notional_usd,
        submitted_at=submitted_at,
        arrival_at=arrival_at,
        expires_at=expires_at,
        limit_price=limit_price,
        execution_version=config.execution_version,
        execution_config_sha256=canonical_hash(execution_config),
    )
    return PaperOrderDraft(
        request=request,
        signal_selected_ask=selected_ask,
        signal_fee_rate=fee_rate,
        signal_slippage_buffer=slippage_buffer,
        execution_config=execution_config,
        market_end_at=market_end_at,
    )


def _validate_book(order: PaperOrderDraft, book: ReplayedBook) -> datetime:
    request = order.request
    if book.condition_id != request.condition_id:
        raise PaperSimulationError("book condition does not match paper order")
    if book.asset_id != request.token_id:
        raise PaperSimulationError("book token does not match paper order")
    cutoff = _utc(book.replay_cutoff_at, name="replay_cutoff_at")
    return cutoff


def _fill_draft(
    order: PaperOrderDraft,
    book: ReplayedBook,
    *,
    shares: Decimal,
    price: Decimal,
    fill_at: datetime,
) -> PaperFillDraft:
    gross_cost = shares * price
    fee = shares * order.signal_fee_rate * price * (_ONE - price)
    return PaperFillDraft(
        fill_at=fill_at,
        shares=shares,
        price=price,
        gross_cost=gross_cost,
        fee=fee,
        total_cost=gross_cost + fee,
        signal_ask_slippage=price - order.signal_selected_ask,
        book_anchor_event_id=book.anchor_event_id,
        book_anchor_dedupe_key=book.anchor_dedupe_key,
        book_applied_event_ids=book.applied_event_ids,
        book_applied_dedupe_keys=book.applied_dedupe_keys,
        replay_cutoff_at=book.replay_cutoff_at,
    )


def simulate_buy(
    order: PaperOrderDraft,
    books: Sequence[ReplayedBook],
    *,
    cancel_at: datetime | None = None,
) -> PaperSimulationResult:
    """Walk only causally new displayed ask depth for a deterministic paper BUY."""

    request = order.request
    cancellation_at: datetime | None = None
    if cancel_at is not None:
        candidate = _utc(cancel_at, name="cancel_at")
        if candidate < request.submitted_at:
            raise PaperSimulationError("cancel_at must not precede order submission")
        if candidate < request.expires_at:
            cancellation_at = candidate

    execution_cutoff = cancellation_at or request.expires_at
    remaining = request.requested_shares
    fills: list[PaperFillDraft] = []
    previous_display: dict[Decimal, Decimal] | None = None
    previous_cutoff: datetime | None = None

    for book in books:
        cutoff = _validate_book(order, book)
        if previous_cutoff is not None and cutoff < previous_cutoff:
            raise PaperSimulationError("book observations must be chronological")
        previous_cutoff = cutoff
        if cutoff < request.arrival_at or cutoff > execution_cutoff:
            continue

        current_display = {level.price: level.size for level in book.asks}
        if previous_display is None:
            available = dict(current_display)
        else:
            available = {
                price: size - previous_display.get(price, _ZERO)
                for price, size in current_display.items()
                if size > previous_display.get(price, _ZERO)
            }
        previous_display = current_display

        for price in sorted(available):
            if remaining <= _ZERO:
                break
            if price > request.limit_price:
                break
            depth = available[price]
            if depth <= _ZERO:
                continue
            shares = min(depth, remaining)
            fills.append(
                _fill_draft(
                    order,
                    book,
                    shares=shares,
                    price=price,
                    fill_at=cutoff,
                )
            )
            remaining -= shares

        if remaining <= _ZERO:
            break

    filled_shares = request.requested_shares - remaining
    total_cost = sum((fill.total_cost for fill in fills), _ZERO)
    if remaining <= _ZERO:
        terminal = _terminal(
            "FILLED",
            reason="requested paper shares fully filled",
            event_at=fills[-1].fill_at,
            remaining_shares=_ZERO,
        )
    elif cancellation_at is not None:
        reason = (
            "cancelled_before_arrival"
            if cancellation_at < request.arrival_at
            else "cancelled_with_remainder"
        )
        terminal = _terminal(
            "CANCELLED",
            reason=reason,
            event_at=cancellation_at,
            remaining_shares=remaining,
        )
    else:
        market_end_at = order.market_end_at
        market_ended = market_end_at is not None and request.expires_at == market_end_at
        if market_ended and filled_shares == _ZERO:
            terminal = _terminal(
                "MARKET_ENDED_UNFILLED",
                reason="market ended before any eligible paper depth filled",
                event_at=request.expires_at,
                remaining_shares=remaining,
            )
        else:
            terminal = _terminal(
                "EXPIRED",
                reason="paper order expired with unfilled shares",
                event_at=request.expires_at,
                remaining_shares=remaining,
            )

    return PaperSimulationResult(
        fills=tuple(fills),
        terminal=terminal,
        filled_shares=filled_shares,
        remaining_shares=remaining,
        total_cost=total_cost,
    )
