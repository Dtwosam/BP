from __future__ import annotations

import math
from collections import Counter

from bp_engine.calibration.models import EdgeDecision, EdgePolicyMetrics
from bp_engine.modeling.models import SupervisedRow


def _flag_clear(row: SupervisedRow, key: str) -> bool:
    value = row.predictors.get(key)
    return value is not None and float(value) == 0.0


def _probability_observation(
    row: SupervisedRow, max_last_trade_age_seconds: int
) -> tuple[bool, float | None, str]:
    if max_last_trade_age_seconds <= 0 or max_last_trade_age_seconds > 10:
        raise ValueError("max_last_trade_age_seconds must be within [1, 10]")
    if not _flag_clear(row, "missing__pm_up_last_trade_missing"):
        return False, None, "last_trade_missing"
    value = row.predictors.get("pm_up_last_trade_price")
    availability_age = row.predictors.get("pm_up_last_trade_availability_age_s")
    source_age = row.predictors.get("pm_up_last_trade_source_age_s")
    if value is None or availability_age is None or source_age is None:
        return False, None, "last_trade_missing"
    probability = float(value)
    age_seconds = float(availability_age)
    source_age_seconds = float(source_age)
    if (
        not math.isfinite(probability)
        or probability < 0.0
        or probability > 1.0
        or not math.isfinite(age_seconds)
        or age_seconds < 0.0
        or not math.isfinite(source_age_seconds)
        or source_age_seconds < 0.0
    ):
        return False, None, "last_trade_invalid"
    if age_seconds > max_last_trade_age_seconds:
        return False, probability, "last_trade_stale"
    return True, probability, "eligible"


def eligible_probability(
    row: SupervisedRow, max_last_trade_age_seconds: int
) -> float | None:
    eligible, probability, _ = _probability_observation(
        row, max_last_trade_age_seconds
    )
    return probability if eligible else None


def edge_decision_v2(
    row: SupervisedRow,
    *,
    calibrated_probability_up: float | None,
    max_last_trade_age_seconds: int,
    fee_rate: float,
    slippage_buffer: float,
    min_edge: float | None,
) -> EdgeDecision:
    eligible, _, eligibility_reason = _probability_observation(
        row, max_last_trade_age_seconds
    )
    if not eligible or calibrated_probability_up is None:
        return EdgeDecision(
            side="none",
            predicted_target=-1,
            side_probability=0.0,
            market_probability_observed=False,
            executable=False,
            trade=False,
            reason=eligibility_reason,
            ask=None,
            bid=None,
            spread=None,
            fee=0.0,
            slippage_buffer=slippage_buffer,
            raw_edge=None,
            cost_adjusted_edge=None,
            min_edge=min_edge,
        )

    probability_up = float(calibrated_probability_up)
    if not math.isfinite(probability_up) or not 0.0 <= probability_up <= 1.0:
        raise ValueError("calibrated_probability_up must be within [0, 1]")
    predicted_target = 1 if probability_up >= 0.5 else 0
    side = "up" if predicted_target == 1 else "down"
    prefix = f"pm_{side}"
    side_probability = probability_up if predicted_target == 1 else 1.0 - probability_up

    if not _flag_clear(row, f"missing__{prefix}_book_missing"):
        reason = "selected_book_missing"
    elif not _flag_clear(row, f"missing__{prefix}_book_stale"):
        reason = "selected_book_stale"
    else:
        reason = ""

    ask_value = row.predictors.get(f"{prefix}_best_ask")
    if reason or ask_value is None:
        return EdgeDecision(
            side=side,
            predicted_target=predicted_target,
            side_probability=side_probability,
            market_probability_observed=True,
            executable=False,
            trade=False,
            reason=reason or "selected_ask_unavailable",
            ask=None,
            bid=None,
            spread=None,
            fee=0.0,
            slippage_buffer=slippage_buffer,
            raw_edge=None,
            cost_adjusted_edge=None,
            min_edge=min_edge,
        )

    ask = float(ask_value)
    if not math.isfinite(ask) or not 0.0 <= ask <= 1.0:
        return EdgeDecision(
            side=side,
            predicted_target=predicted_target,
            side_probability=side_probability,
            market_probability_observed=True,
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
    bid_value = row.predictors.get(f"{prefix}_best_bid")
    bid = None if bid_value is None else float(bid_value)
    if bid is not None and (not math.isfinite(bid) or not 0.0 <= bid <= ask):
        bid = None
    spread = ask - bid if bid is not None else None
    fee = fee_rate * ask * (1.0 - ask)
    raw_edge = side_probability - ask
    adjusted = raw_edge - fee - slippage_buffer
    if min_edge is None:
        trade = False
        reason = "policy_no_trade"
    else:
        trade = adjusted >= min_edge
        reason = "trade" if trade else "edge_below_minimum"
    return EdgeDecision(
        side=side,
        predicted_target=predicted_target,
        side_probability=side_probability,
        market_probability_observed=True,
        executable=True,
        trade=trade,
        reason=reason,
        ask=ask,
        bid=bid,
        spread=spread,
        fee=fee,
        slippage_buffer=slippage_buffer,
        raw_edge=raw_edge,
        cost_adjusted_edge=adjusted,
        min_edge=min_edge,
    )


def evaluate_edge_policy_v2(
    rows: tuple[SupervisedRow, ...],
    calibrated_by_condition: dict[str, float],
    *,
    max_last_trade_age_seconds: int,
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
        decision = edge_decision_v2(
            row,
            calibrated_probability_up=calibrated_by_condition.get(row.condition_id),
            max_last_trade_age_seconds=max_last_trade_age_seconds,
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
        is_correct = row.target == decision.predicted_target
        correct += int(is_correct)
        payout = 1.0 if is_correct else 0.0
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
        mean_realized_pnl_after_assumed_costs=sum(cost_pnl) / trades if trades else None,
    )
