from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from bp_engine.calibration.models import EdgeDecision, EdgePolicyMetrics
from bp_engine.modeling.models import SupervisedRow


@dataclass(frozen=True)
class V3ExecutionBook:
    up_best_bid: float | None
    up_best_ask: float | None
    up_fresh: bool
    down_best_bid: float | None
    down_best_ask: float | None
    down_fresh: bool


def edge_decision_v3(
    *,
    calibrated_probability_up: float,
    book: V3ExecutionBook,
    fee_rate: float,
    slippage_buffer: float,
    min_edge: float | None,
) -> EdgeDecision:
    probability_up = float(calibrated_probability_up)
    if not math.isfinite(probability_up) or not 0.0 <= probability_up <= 1.0:
        raise ValueError("calibrated_probability_up must be within [0, 1]")
    predicted_target = 1 if probability_up >= 0.5 else 0
    side = "up" if predicted_target == 1 else "down"
    side_probability = probability_up if predicted_target == 1 else 1.0 - probability_up

    fresh = book.up_fresh if predicted_target == 1 else book.down_fresh
    ask = book.up_best_ask if predicted_target == 1 else book.down_best_ask
    bid = book.up_best_bid if predicted_target == 1 else book.down_best_bid
    if not fresh:
        return EdgeDecision(
            side=side,
            predicted_target=predicted_target,
            side_probability=side_probability,
            market_probability_observed=False,
            executable=False,
            trade=False,
            reason="selected_book_stale_or_missing",
            ask=None,
            bid=None,
            spread=None,
            fee=0.0,
            slippage_buffer=slippage_buffer,
            raw_edge=None,
            cost_adjusted_edge=None,
            min_edge=min_edge,
        )
    if ask is None or not math.isfinite(float(ask)) or not 0.0 <= float(ask) <= 1.0:
        return EdgeDecision(
            side=side,
            predicted_target=predicted_target,
            side_probability=side_probability,
            market_probability_observed=False,
            executable=False,
            trade=False,
            reason="selected_ask_unavailable",
            ask=None,
            bid=None,
            spread=None,
            fee=0.0,
            slippage_buffer=slippage_buffer,
            raw_edge=None,
            cost_adjusted_edge=None,
            min_edge=min_edge,
        )

    ask_value = float(ask)
    bid_value = None if bid is None else float(bid)
    if bid_value is not None and (
        not math.isfinite(bid_value) or not 0.0 <= bid_value <= ask_value
    ):
        bid_value = None
    spread = ask_value - bid_value if bid_value is not None else None
    fee = fee_rate * ask_value * (1.0 - ask_value)
    raw_edge = side_probability - ask_value
    adjusted = raw_edge - fee - slippage_buffer
    trade = min_edge is not None and adjusted >= min_edge
    reason = (
        "policy_no_trade"
        if min_edge is None
        else ("trade" if trade else "edge_below_minimum")
    )
    return EdgeDecision(
        side=side,
        predicted_target=predicted_target,
        side_probability=side_probability,
        market_probability_observed=True,
        executable=True,
        trade=trade,
        reason=reason,
        ask=ask_value,
        bid=bid_value,
        spread=spread,
        fee=fee,
        slippage_buffer=slippage_buffer,
        raw_edge=raw_edge,
        cost_adjusted_edge=adjusted,
        min_edge=min_edge,
    )


def evaluate_edge_policy_v3(
    rows: tuple[SupervisedRow, ...],
    calibrated_by_condition: dict[str, float],
    books_by_condition: dict[str, V3ExecutionBook],
    *,
    fee_rate: float,
    slippage_buffer: float,
    min_edge: float | None,
) -> EdgePolicyMetrics:
    reasons: Counter[str] = Counter()
    asks: list[float] = []
    spreads: list[float] = []
    raw_edges: list[float] = []
    adjusted_edges: list[float] = []
    fees: list[float] = []
    gross_pnl: list[float] = []
    cost_pnl: list[float] = []
    observed = executable = correct = 0

    for row in rows:
        probability = calibrated_by_condition.get(row.condition_id)
        book = books_by_condition.get(row.condition_id)
        if probability is None or book is None:
            reasons["forecast_or_book_missing"] += 1
            continue
        decision = edge_decision_v3(
            calibrated_probability_up=probability,
            book=book,
            fee_rate=fee_rate,
            slippage_buffer=slippage_buffer,
            min_edge=min_edge,
        )
        reasons[decision.reason] += 1
        observed += int(decision.market_probability_observed)
        if not decision.executable:
            continue
        executable += 1
        assert decision.ask is not None
        asks.append(decision.ask)
        if decision.spread is not None:
            spreads.append(decision.spread)
        if not decision.trade:
            continue
        assert decision.raw_edge is not None
        assert decision.cost_adjusted_edge is not None
        raw_edges.append(decision.raw_edge)
        adjusted_edges.append(decision.cost_adjusted_edge)
        fees.append(decision.fee)
        correct_trade = row.target == decision.predicted_target
        correct += int(correct_trade)
        payout = 1.0 if correct_trade else 0.0
        gross = payout - decision.ask
        gross_pnl.append(gross)
        cost_pnl.append(gross - decision.fee - slippage_buffer)

    total = len(rows)
    trades = len(gross_pnl)
    no_fill = total - executable
    abstained = executable - trades
    return EdgePolicyMetrics(
        prediction_markets=total,
        market_probability_observed_markets=observed,
        executable_markets=executable,
        trade_count=trades,
        no_fill_markets=no_fill,
        abstained_edge_markets=abstained,
        reason_counts=dict(sorted(reasons.items())),
        trade_coverage=trades / total if total else 0.0,
        average_observed_ask=sum(asks) / len(asks) if asks else None,
        average_observed_spread=sum(spreads) / len(spreads) if spreads else None,
        correct_trades=correct,
        traded_accuracy=correct / trades if trades else None,
        raw_expected_edge_sum=sum(raw_edges),
        mean_raw_expected_edge=sum(raw_edges) / trades if trades else None,
        fee_sum=sum(fees),
        slippage_sum=slippage_buffer * trades,
        cost_adjusted_expected_edge_sum=sum(adjusted_edges),
        mean_cost_adjusted_expected_edge=sum(adjusted_edges) / trades if trades else None,
        gross_realized_pnl_before_costs=sum(gross_pnl),
        realized_pnl_after_assumed_costs=sum(cost_pnl),
        mean_realized_pnl_after_assumed_costs=(
            sum(cost_pnl) / trades if trades else None
        ),
    )


def edge_band_report_v3(
    rows: tuple[SupervisedRow, ...],
    calibrated_by_condition: dict[str, float],
    books_by_condition: dict[str, V3ExecutionBook],
    *,
    fee_rate: float,
    slippage_buffer: float,
    boundaries: tuple[float, ...],
) -> list[dict[str, float | int | str | None]]:
    if tuple(sorted(set(boundaries))) != boundaries:
        raise ValueError("edge band boundaries must be sorted and unique")

    buckets: list[dict[str, object]] = []
    labels = [f"<{boundaries[0]:g}"] if boundaries else ["all"]
    if boundaries:
        labels.extend(
            f"[{lower:g},{upper:g})"
            for lower, upper in zip(boundaries, boundaries[1:], strict=False)
        )
        labels.append(f">={boundaries[-1]:g}")
    for label in labels:
        buckets.append(
            {
                "band": label,
                "count": 0,
                "correct": 0,
                "edge_sum": 0.0,
                "pnl_sum": 0.0,
            }
        )

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
            min_edge=None,
        )
        if (
            not decision.executable
            or decision.ask is None
            or decision.cost_adjusted_edge is None
        ):
            continue

        edge = decision.cost_adjusted_edge
        if not boundaries:
            index = 0
        elif edge < boundaries[0]:
            index = 0
        else:
            index = len(boundaries)
            for candidate_index, upper in enumerate(boundaries[1:], start=1):
                if edge < upper:
                    index = candidate_index
                    break

        correct = row.target == decision.predicted_target
        payout = 1.0 if correct else 0.0
        realized = payout - decision.ask - decision.fee - slippage_buffer
        bucket = buckets[index]
        bucket["count"] = int(bucket["count"]) + 1
        bucket["correct"] = int(bucket["correct"]) + int(correct)
        bucket["edge_sum"] = float(bucket["edge_sum"]) + edge
        bucket["pnl_sum"] = float(bucket["pnl_sum"]) + realized

    report: list[dict[str, float | int | str | None]] = []
    for bucket in buckets:
        count = int(bucket["count"])
        correct = int(bucket["correct"])
        report.append(
            {
                "band": str(bucket["band"]),
                "count": count,
                "correct": correct,
                "accuracy": correct / count if count else None,
                "mean_cost_adjusted_edge": (
                    float(bucket["edge_sum"]) / count if count else None
                ),
                "realized_pnl_after_assumed_costs": float(bucket["pnl_sum"]),
            }
        )
    return report
