from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from bp_engine.execution.paper import (
    PaperFillDraft,
    PaperOrderDraft,
    PaperTerminalDraft,
    build_paper_order,
    simulate_buy,
)

from bp_engine.execution.book import BookLevel, ReplayedBook
from bp_engine.execution.models import ExecutionOrderRequest, PaperExecutionConfig
from bp_engine.features.hashing import canonical_hash

BASE = datetime(2026, 8, 28, 17, 0, tzinfo=UTC)


def _prediction(**overrides: object) -> dict[str, object]:
    prediction: dict[str, object] = {
        "prediction_id": "prediction-1",
        "semantic_sha256": "a" * 64,
        "condition_id": "condition-1",
        "recorded_at": BASE,
        "market_end_at": BASE + timedelta(seconds=10),
        "up_token_id": "up-token",
        "down_token_id": "down-token",
        "selected_side": "up",
        "trade": True,
        "executable": True,
        "selected_ask": Decimal("0.50"),
        "slippage_buffer": Decimal("0.01"),
        "edge_config": {"fee_rate": Decimal("0.07")},
    }
    prediction.update(overrides)
    return prediction


def _book(
    *,
    cutoff: datetime,
    asks: tuple[tuple[str, str], ...],
    event_id: int,
    applied_event_ids: tuple[int, ...] = (),
) -> ReplayedBook:
    return ReplayedBook(
        condition_id="condition-1",
        asset_id="up-token",
        bids=(BookLevel(price=Decimal("0.50"), size=Decimal("20")),),
        asks=tuple(
            BookLevel(price=Decimal(price), size=Decimal(size)) for price, size in asks
        ),
        anchor_event_id=event_id,
        anchor_dedupe_key=f"{event_id:064x}",
        applied_event_ids=applied_event_ids,
        applied_dedupe_keys=tuple(f"{value:064x}" for value in applied_event_ids),
        replay_cutoff_at=cutoff,
    )


def _order(*, requested_shares: Decimal = Decimal("10")) -> PaperOrderDraft:
    request = ExecutionOrderRequest(
        prediction_id="prediction-1",
        prediction_semantic_sha256="a" * 64,
        condition_id="condition-1",
        token_id="up-token",
        selected_side="up",
        action="BUY",
        requested_shares=requested_shares,
        target_notional_usd=Decimal("10"),
        submitted_at=BASE,
        arrival_at=BASE + timedelta(milliseconds=250),
        expires_at=BASE + timedelta(seconds=2, milliseconds=250),
        limit_price=Decimal("0.58"),
        execution_version="paper-execution-v1",
        execution_config_sha256="b" * 64,
    )
    return PaperOrderDraft(
        request=request,
        signal_selected_ask=Decimal("0.56"),
        signal_fee_rate=Decimal("0.07"),
        signal_slippage_buffer=Decimal("0.02"),
        execution_config={"execution_version": "paper-execution-v1"},
    )


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"trade": False}, "trade=false"),
        ({"executable": False}, "executable=false"),
        ({"selected_side": "sideways"}, "selected_side"),
        ({"selected_ask": None}, "selected_ask"),
        ({"edge_config": {}}, "fee_rate"),
    ],
)
def test_build_paper_order_fails_closed_for_ineligible_signal(
    overrides: dict[str, object],
    reason: str,
) -> None:
    result = build_paper_order(
        _prediction(**overrides),
        PaperExecutionConfig(),
        Decimal("100"),
    )

    assert isinstance(result, PaperTerminalDraft)
    assert result.status == "SKIPPED"
    assert reason in result.reason


def test_build_paper_order_sizes_to_worst_case_cash_and_target() -> None:
    config = PaperExecutionConfig(
        starting_cash_usd=Decimal("100"),
        target_notional_usd=Decimal("5"),
        latency_ms=250,
        order_ttl_ms=2000,
        share_precision=6,
    )

    result = build_paper_order(_prediction(), config, Decimal("100"))

    assert isinstance(result, PaperOrderDraft)
    assert result.request.submitted_at == BASE
    assert result.request.arrival_at == BASE + timedelta(milliseconds=250)
    assert result.request.expires_at == BASE + timedelta(seconds=2, milliseconds=250)
    assert result.request.limit_price == Decimal("0.51")
    assert result.request.requested_shares == Decimal("9.478798")
    assert result.request.token_id == "up-token"
    assert result.signal_selected_ask == Decimal("0.50")
    assert result.signal_fee_rate == Decimal("0.07")
    assert result.signal_slippage_buffer == Decimal("0.01")
    assert result.request.execution_config_sha256 == canonical_hash(config.as_mapping())

    worst_fee_per_share = Decimal("0.07") * Decimal("0.51") * Decimal("0.49")
    worst_total_per_share = Decimal("0.51") + worst_fee_per_share
    worst_total = result.request.requested_shares * worst_total_per_share
    assert worst_total <= config.target_notional_usd
    assert worst_total <= Decimal("100")


def test_build_paper_order_fails_when_cash_cannot_buy_minimum_share() -> None:
    config = PaperExecutionConfig(share_precision=6)

    result = build_paper_order(_prediction(), config, Decimal("0"))

    assert isinstance(result, PaperTerminalDraft)
    assert result.status == "INSUFFICIENT_PAPER_CASH"
    assert result.remaining_shares == Decimal("0")


def test_simulate_buy_walks_depth_once_and_expires_partial_remainder() -> None:
    order = _order()
    first = _book(
        cutoff=order.request.arrival_at,
        asks=(("0.56", "2"), ("0.57", "3"), ("0.59", "10")),
        event_id=101,
    )
    unchanged = _book(
        cutoff=order.request.arrival_at + timedelta(milliseconds=500),
        asks=(("0.56", "2"), ("0.57", "3"), ("0.59", "10")),
        event_id=101,
    )

    result = simulate_buy(order, (first, unchanged))

    assert result.filled_shares == Decimal("5")
    assert result.remaining_shares == Decimal("5")
    assert result.terminal.status == "EXPIRED"
    assert result.terminal.event_at == order.request.expires_at
    assert len(result.fills) == 2
    first_fill, second_fill = result.fills
    assert isinstance(first_fill, PaperFillDraft)
    assert first_fill.shares == Decimal("2")
    assert first_fill.price == Decimal("0.56")
    assert first_fill.gross_cost == Decimal("1.12")
    assert first_fill.fee == Decimal("0.034496")
    assert first_fill.total_cost == Decimal("1.154496")
    assert first_fill.signal_ask_slippage == Decimal("0.00")
    assert second_fill.shares == Decimal("3")
    assert second_fill.price == Decimal("0.57")
    assert second_fill.gross_cost == Decimal("1.71")
    assert second_fill.fee == Decimal("0.051471")
    assert second_fill.total_cost == Decimal("1.761471")
    assert second_fill.signal_ask_slippage == Decimal("0.01")
    assert all(fill.price <= order.request.limit_price for fill in result.fills)
    assert all(
        order.request.arrival_at <= fill.fill_at <= order.request.expires_at
        for fill in result.fills
    )


def test_simulate_buy_uses_only_new_or_replenished_later_depth() -> None:
    order = _order()
    first = _book(
        cutoff=order.request.arrival_at,
        asks=(("0.56", "2"), ("0.57", "3"), ("0.59", "10")),
        event_id=101,
    )
    replenished = _book(
        cutoff=order.request.arrival_at + timedelta(milliseconds=500),
        asks=(("0.56", "2"), ("0.57", "5"), ("0.58", "4"), ("0.59", "10")),
        event_id=101,
        applied_event_ids=(102,),
    )

    result = simulate_buy(order, (first, replenished))

    assert result.filled_shares == Decimal("10")
    assert result.remaining_shares == Decimal("0")
    assert result.terminal.status == "FILLED"
    assert [(fill.price, fill.shares) for fill in result.fills] == [
        (Decimal("0.56"), Decimal("2")),
        (Decimal("0.57"), Decimal("3")),
        (Decimal("0.57"), Decimal("2")),
        (Decimal("0.58"), Decimal("3")),
    ]
    assert result.fills[-1].fill_at == replenished.replay_cutoff_at
