from __future__ import annotations

from dataclasses import asdict
from typing import Any

from bp_engine.modeling.models import SupervisedRow
from bp_engine.v3_research.policy import (
    V3ExecutionBook,
    edge_decision_v3,
    evaluate_edge_policy_v3,
)

V4ExecutionBook = V3ExecutionBook


def _max_drawdown(values: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def _max_losing_streak(values: list[float]) -> int:
    current = 0
    maximum = 0
    for value in values:
        if value < 0.0:
            current += 1
            maximum = max(maximum, current)
        else:
            current = 0
    return maximum


def evaluate_economics_v4(
    rows: tuple[SupervisedRow, ...],
    calibrated_by_condition: dict[str, float],
    books_by_condition: dict[str, V4ExecutionBook],
    *,
    fee_rate: float,
    slippage_buffer: float,
    min_edge: float | None,
) -> dict[str, Any]:
    base = evaluate_edge_policy_v3(
        rows,
        calibrated_by_condition,
        books_by_condition,
        fee_rate=fee_rate,
        slippage_buffer=slippage_buffer,
        min_edge=min_edge,
    )
    pnl: list[float] = []
    wins: list[float] = []
    losses: list[float] = []
    trade_spreads: list[float] = []
    trade_asks: list[float] = []
    side_counts = {"up": 0, "down": 0}
    side_pnl = {"up": 0.0, "down": 0.0}

    for row in rows:
        probability = calibrated_by_condition.get(row.condition_id)
        book = books_by_condition.get(row.condition_id)
        if probability is None or book is None:
            continue
        decision = edge_decision_v3(
            calibrated_probability_up=probability,
            book=book,
            fee_rate=fee_rate,
            slippage_buffer=slippage_buffer,
            min_edge=min_edge,
        )
        if not decision.trade:
            continue
        assert decision.ask is not None
        correct = row.target == decision.predicted_target
        realized = (
            (1.0 if correct else 0.0)
            - decision.ask
            - decision.fee
            - slippage_buffer
        )
        pnl.append(realized)
        (wins if realized > 0.0 else losses).append(realized)
        side_counts[decision.side] += 1
        side_pnl[decision.side] += realized
        trade_asks.append(decision.ask)
        if decision.spread is not None:
            trade_spreads.append(decision.spread)

    gross_profit = sum(wins)
    gross_loss = -sum(losses)
    return {
        **asdict(base),
        "wins": len(wins),
        "losses": len(losses),
        "average_win": sum(wins) / len(wins) if wins else None,
        "average_loss": sum(losses) / len(losses) if losses else None,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": (
            None if gross_loss == 0.0 else gross_profit / gross_loss
        ),
        "max_drawdown": _max_drawdown(pnl),
        "max_losing_streak": _max_losing_streak(pnl),
        "largest_win": max(wins) if wins else None,
        "largest_loss": min(losses) if losses else None,
        "average_trade_ask": (
            sum(trade_asks) / len(trade_asks) if trade_asks else None
        ),
        "average_trade_spread": (
            sum(trade_spreads) / len(trade_spreads) if trade_spreads else None
        ),
        "trade_side_counts": side_counts,
        "trade_side_pnl": side_pnl,
        "execution_availability": {
            "prediction_markets": base.prediction_markets,
            "market_probability_observed_markets": (
                base.market_probability_observed_markets
            ),
            "executable_markets": base.executable_markets,
            "selected_book_or_ask_failure_markets": base.no_fill_markets,
            "reason_counts": dict(base.reason_counts),
        },
    }
