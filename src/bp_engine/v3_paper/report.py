from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, select

from bp_engine.execution.models import V3_FROZEN_PAPER_STARTING_CASH_USD
from bp_engine.storage import schema
from bp_engine.v3_paper.service import (
    V3_PAPER_EXECUTION_VERSION,
    V3_PAPER_PREDICTION_VERSION,
)

_ZERO = Decimal("0")


def _decimal(value: object | None) -> Decimal:
    if value is None:
        return _ZERO
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _latest_by(
    rows: list[dict[str, Any]],
    *,
    key: str,
    timestamp: str,
) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        identity = str(row[key])
        existing = latest.get(identity)
        if existing is None or _utc(row[timestamp]) > _utc(existing[timestamp]):
            latest[identity] = row
    return latest


def _side_summary() -> dict[str, dict[str, Decimal | int]]:
    return {
        "up": {"trades": 0, "wins": 0, "losses": 0, "breakeven": 0, "realized_pnl": _ZERO},
        "down": {"trades": 0, "wins": 0, "losses": 0, "breakeven": 0, "realized_pnl": _ZERO},
    }


def build_v3_paper_report(
    engine: Engine,
    *,
    recent_limit: int = 20,
) -> dict[str, Any]:
    """Build a read-only report for the isolated frozen V3 paper epoch."""
    limit = max(1, min(int(recent_limit), 200))

    with engine.connect() as connection:
        predictions = [
            dict(row)
            for row in connection.execute(
                select(schema.live_predictions)
                .where(
                    schema.live_predictions.c.prediction_version
                    == V3_PAPER_PREDICTION_VERSION
                )
                .order_by(
                    schema.live_predictions.c.scheduled_at,
                    schema.live_predictions.c.id,
                )
            ).mappings()
        ]
        prediction_ids = tuple(str(row["prediction_id"]) for row in predictions)

        evaluations = []
        if prediction_ids:
            evaluations = [
                dict(row)
                for row in connection.execute(
                    select(schema.live_prediction_evaluations)
                    .where(
                        schema.live_prediction_evaluations.c.prediction_id.in_(
                            prediction_ids
                        )
                    )
                    .order_by(
                        schema.live_prediction_evaluations.c.evaluated_at,
                        schema.live_prediction_evaluations.c.id,
                    )
                ).mappings()
            ]

        orders = [
            dict(row)
            for row in connection.execute(
                select(schema.paper_orders)
                .where(
                    schema.paper_orders.c.execution_version
                    == V3_PAPER_EXECUTION_VERSION
                )
                .order_by(schema.paper_orders.c.submitted_at, schema.paper_orders.c.id)
            ).mappings()
        ]
        order_ids = tuple(str(row["paper_order_id"]) for row in orders)

        fills: list[dict[str, Any]] = []
        terminals: list[dict[str, Any]] = []
        settlements: list[dict[str, Any]] = []
        if order_ids:
            fills = [
                dict(row)
                for row in connection.execute(
                    select(schema.paper_fills)
                    .where(schema.paper_fills.c.paper_order_id.in_(order_ids))
                    .order_by(schema.paper_fills.c.fill_at, schema.paper_fills.c.id)
                ).mappings()
            ]
            terminals = [
                dict(row)
                for row in connection.execute(
                    select(schema.paper_order_terminal_events)
                    .where(
                        schema.paper_order_terminal_events.c.paper_order_id.in_(
                            order_ids
                        )
                    )
                    .order_by(
                        schema.paper_order_terminal_events.c.event_at,
                        schema.paper_order_terminal_events.c.id,
                    )
                ).mappings()
            ]
            settlements = [
                dict(row)
                for row in connection.execute(
                    select(schema.paper_settlements)
                    .where(schema.paper_settlements.c.paper_order_id.in_(order_ids))
                    .order_by(
                        schema.paper_settlements.c.settled_at,
                        schema.paper_settlements.c.id,
                    )
                ).mappings()
            ]

    prediction_by_id = {
        str(row["prediction_id"]): row
        for row in predictions
    }
    latest_evaluation = _latest_by(
        evaluations,
        key="prediction_id",
        timestamp="evaluated_at",
    )
    order_by_id = {str(row["paper_order_id"]): row for row in orders}
    latest_settlement = _latest_by(
        settlements,
        key="paper_order_id",
        timestamp="settled_at",
    )

    fills_by_order: dict[str, list[dict[str, Any]]] = {}
    for fill in fills:
        fills_by_order.setdefault(str(fill["paper_order_id"]), []).append(fill)

    trade_predictions = [row for row in predictions if row["trade"] is True]
    evaluated_trade_predictions = [
        row
        for row in trade_predictions
        if str(row["prediction_id"]) in latest_evaluation
    ]
    correct_trade_signals = sum(
        int(latest_evaluation[str(row["prediction_id"])]["correct"] is True)
        for row in evaluated_trade_predictions
    )

    settled_orders = [
        order_by_id[order_id]
        for order_id in latest_settlement
        if order_id in order_by_id
    ]
    realized_pnl = sum(
        (_decimal(row["realized_pnl"]) for row in latest_settlement.values()),
        _ZERO,
    )
    settled_cost = sum(
        (_decimal(row["total_fill_cost"]) for row in latest_settlement.values()),
        _ZERO,
    )
    all_fill_cost = sum((_decimal(row["total_cost"]) for row in fills), _ZERO)
    all_payouts = sum(
        (_decimal(row["payout"]) for row in latest_settlement.values()),
        _ZERO,
    )
    starting_cash = V3_FROZEN_PAPER_STARTING_CASH_USD
    current_cash = starting_cash - all_fill_cost + all_payouts

    settled_order_ids = set(latest_settlement)
    open_cost = sum(
        (
            _decimal(fill["total_cost"])
            for order_id, order_fills in fills_by_order.items()
            if order_id not in settled_order_ids
            for fill in order_fills
        ),
        _ZERO,
    )

    wins = losses = breakeven = 0
    by_side = _side_summary()
    recent: list[dict[str, Any]] = []
    for order in settled_orders:
        order_id = str(order["paper_order_id"])
        settlement = latest_settlement[order_id]
        pnl = _decimal(settlement["realized_pnl"])
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1
        else:
            breakeven += 1

        side = str(order["selected_side"]).lower()
        side_bucket = by_side[side]
        side_bucket["trades"] = int(side_bucket["trades"]) + 1
        side_bucket["wins"] = int(side_bucket["wins"]) + int(pnl > 0)
        side_bucket["losses"] = int(side_bucket["losses"]) + int(pnl < 0)
        side_bucket["breakeven"] = int(side_bucket["breakeven"]) + int(pnl == 0)
        side_bucket["realized_pnl"] = _decimal(side_bucket["realized_pnl"]) + pnl

        prediction = prediction_by_id.get(str(order["prediction_id"]))
        evaluation = latest_evaluation.get(str(order["prediction_id"]))
        recent.append(
            {
                "paper_order_id": order_id,
                "prediction_id": str(order["prediction_id"]),
                "condition_id": str(order["condition_id"]),
                "selected_side": side,
                "signal_probability": (
                    prediction.get("calibrated_probability")
                    if prediction is not None
                    else None
                ),
                "signal_edge": (
                    prediction.get("cost_adjusted_edge")
                    if prediction is not None
                    else None
                ),
                "filled_shares": settlement["filled_shares"],
                "total_fill_cost": settlement["total_fill_cost"],
                "official_outcome": settlement["official_outcome"],
                "correct": evaluation.get("correct") if evaluation is not None else None,
                "payout": settlement["payout"],
                "realized_pnl": settlement["realized_pnl"],
                "settled_at": settlement["settled_at"],
            }
        )

    recent.sort(key=lambda row: _utc(row["settled_at"]), reverse=True)

    decision_reasons = Counter(str(row["decision_reason"]) for row in predictions)
    terminal_statuses = Counter(str(row["terminal_status"]) for row in terminals)
    invalid_order_sources = sum(
        int(
            str(order["prediction_id"]) not in prediction_by_id
        )
        for order in orders
    )

    return {
        "prediction_version": V3_PAPER_PREDICTION_VERSION,
        "execution_version": V3_PAPER_EXECUTION_VERSION,
        "summary": {
            "prediction_count": len(predictions),
            "executable_prediction_count": sum(
                int(row["executable"] is True) for row in predictions
            ),
            "trade_signal_count": len(trade_predictions),
            "evaluated_trade_signal_count": len(evaluated_trade_predictions),
            "correct_trade_signal_count": correct_trade_signals,
            "trade_signal_accuracy": (
                correct_trade_signals / len(evaluated_trade_predictions)
                if evaluated_trade_predictions
                else None
            ),
            "paper_order_count": len(orders),
            "paper_fill_count": len(fills),
            "filled_order_count": len(fills_by_order),
            "settled_order_count": len(latest_settlement),
            "open_filled_order_count": sum(
                int(order_id not in settled_order_ids)
                for order_id in fills_by_order
            ),
            "wins": wins,
            "losses": losses,
            "breakeven": breakeven,
            "realized_pnl": realized_pnl,
            "settled_fill_cost": settled_cost,
            "settled_return_on_cost": (
                realized_pnl / settled_cost if settled_cost > 0 else None
            ),
            "virtual_starting_cash": starting_cash,
            "virtual_current_cash": current_cash,
            "open_fill_cost": open_cost,
        },
        "by_side": by_side,
        "decision_reason_counts": dict(sorted(decision_reasons.items())),
        "terminal_status_counts": dict(sorted(terminal_statuses.items())),
        "integrity": {
            "invalid_order_source_count": invalid_order_sources,
        },
        "recent_settled_trades": recent[:limit],
    }
