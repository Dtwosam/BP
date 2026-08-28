from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bp_engine.execution.book import BookLevel, ReplayedBook
from bp_engine.execution.models import ExecutionOrderRequest
from bp_engine.execution.paper import PaperOrderDraft, simulate_buy

BASE = datetime(2026, 8, 28, 19, 0, tzinfo=UTC)


def _order(*, requested_shares: Decimal = Decimal("5")) -> PaperOrderDraft:
    request = ExecutionOrderRequest(
        prediction_id="prediction-cancel",
        prediction_semantic_sha256="a" * 64,
        condition_id="condition-cancel",
        token_id="up-token",
        selected_side="up",
        action="BUY",
        requested_shares=requested_shares,
        target_notional_usd=Decimal("5"),
        submitted_at=BASE,
        arrival_at=BASE + timedelta(milliseconds=250),
        expires_at=BASE + timedelta(seconds=2, milliseconds=250),
        limit_price=Decimal("0.60"),
        execution_version="paper-execution-v1",
        execution_config_sha256="b" * 64,
    )
    return PaperOrderDraft(
        request=request,
        signal_selected_ask=Decimal("0.55"),
        signal_fee_rate=Decimal("0.07"),
        signal_slippage_buffer=Decimal("0.05"),
        execution_config={"execution_version": "paper-execution-v1"},
        market_end_at=BASE + timedelta(seconds=10),
    )


def _book(*, cutoff: datetime, asks: tuple[tuple[str, str], ...], event_id: int) -> ReplayedBook:
    return ReplayedBook(
        condition_id="condition-cancel",
        asset_id="up-token",
        bids=(BookLevel(price=Decimal("0.54"), size=Decimal("20")),),
        asks=tuple(BookLevel(price=Decimal(price), size=Decimal(size)) for price, size in asks),
        anchor_event_id=event_id,
        anchor_dedupe_key=f"sha256:{event_id:064x}",
        applied_event_ids=(),
        applied_dedupe_keys=(),
        replay_cutoff_at=cutoff,
    )


def test_cancel_before_arrival_prevents_any_fill() -> None:
    order = _order()
    cancel_at = BASE + timedelta(milliseconds=100)
    book = _book(
        cutoff=order.request.arrival_at,
        asks=(("0.55", "10"),),
        event_id=101,
    )

    result = simulate_buy(order, (book,), cancel_at=cancel_at)

    assert result.filled_shares == Decimal("0")
    assert result.remaining_shares == Decimal("5")
    assert result.fills == ()
    assert result.terminal.status == "CANCELLED"
    assert result.terminal.event_at == cancel_at
    assert result.terminal.reason == "cancelled_before_arrival"


def test_cancel_after_partial_fill_preserves_fill_and_cancels_remainder() -> None:
    order = _order()
    cancel_at = BASE + timedelta(milliseconds=700)
    arrival_book = _book(
        cutoff=order.request.arrival_at,
        asks=(("0.55", "2"), ("0.61", "10")),
        event_id=101,
    )
    post_cancel_book = _book(
        cutoff=BASE + timedelta(milliseconds=900),
        asks=(("0.55", "10"),),
        event_id=102,
    )

    result = simulate_buy(
        order,
        (arrival_book, post_cancel_book),
        cancel_at=cancel_at,
    )

    assert result.filled_shares == Decimal("2")
    assert result.remaining_shares == Decimal("3")
    assert [(fill.price, fill.shares) for fill in result.fills] == [
        (Decimal("0.55"), Decimal("2")),
    ]
    assert result.terminal.status == "CANCELLED"
    assert result.terminal.event_at == cancel_at
    assert result.terminal.reason == "cancelled_with_remainder"


def test_cancel_cannot_rewrite_order_that_filled_before_cancel() -> None:
    order = _order()
    cancel_at = BASE + timedelta(milliseconds=700)
    arrival_book = _book(
        cutoff=order.request.arrival_at,
        asks=(("0.55", "5"),),
        event_id=101,
    )

    result = simulate_buy(order, (arrival_book,), cancel_at=cancel_at)

    assert result.filled_shares == Decimal("5")
    assert result.remaining_shares == Decimal("0")
    assert result.terminal.status == "FILLED"
    assert result.terminal.event_at == order.request.arrival_at
