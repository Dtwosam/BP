from __future__ import annotations

import math
from collections import Counter

from bp_engine.calibration.models import (
    EdgeConfig,
    EdgeDecision,
    EdgePolicyMetrics,
    EdgePolicySelection,
    EdgeThresholdCandidate,
)
from bp_engine.modeling.models import SupervisedRow


def _finite_probability(value: float) -> float:
    numeric = float(value)
    if not math.isfinite(numeric) or not 0.0 <= numeric <= 1.0:
        raise ValueError("probability must be finite and within [0, 1]")
    return numeric


def _observed_market_probability(row: SupervisedRow) -> bool:
    value = row.predictors.get("pm_up_price")
    if value is None:
        return False
    numeric = float(value)
    return math.isfinite(numeric) and 0.0 <= numeric <= 1.0


def _clear_flag(row: SupervisedRow, key: str) -> bool:
    value = row.predictors.get(key)
    if value is None:
        return False
    numeric = float(value)
    return math.isfinite(numeric) and numeric == 0.0


def _valid_ask(value: object) -> float | None:
    if value is None:
        return None
    numeric = float(value)
    if not math.isfinite(numeric) or not 0.0 < numeric <= 1.0:
        return None
    return numeric


def _valid_bid(value: object, ask: float) -> float | None:
    if value is None:
        return None
    numeric = float(value)
    if not math.isfinite(numeric) or not 0.0 <= numeric <= ask:
        return None
    return numeric


def _unavailable(
    *,
    side: str,
    predicted_target: int,
    side_probability: float,
    market_probability_observed: bool,
    reason: str,
    config: EdgeConfig,
    min_edge: float | None,
) -> EdgeDecision:
    return EdgeDecision(
        side=side,
        predicted_target=predicted_target,
        side_probability=side_probability,
        market_probability_observed=market_probability_observed,
        executable=False,
        trade=False,
        reason=reason,
        ask=None,
        bid=None,
        spread=None,
        fee=0.0,
        slippage_buffer=config.slippage_buffer,
        raw_edge=None,
        cost_adjusted_edge=None,
        min_edge=min_edge,
    )


def edge_decision(
    row: SupervisedRow,
    probability_up: float,
    config: EdgeConfig,
    min_edge: float | None,
) -> EdgeDecision:
    probability_up = _finite_probability(probability_up)
    if min_edge is not None and (not math.isfinite(min_edge) or min_edge < 0):
        raise ValueError("min_edge must be finite and non-negative")

    side = "up" if probability_up >= 0.5 else "down"
    prefix = f"pm_{side}"
    predicted_target = 1 if side == "up" else 0
    side_probability = probability_up if side == "up" else 1.0 - probability_up

    if not _observed_market_probability(row):
        return _unavailable(
            side=side,
            predicted_target=predicted_target,
            side_probability=side_probability,
            market_probability_observed=False,
            reason="missing_market_probability",
            config=config,
            min_edge=min_edge,
        )
    if not _clear_flag(row, f"missing__{prefix}_book_missing"):
        return _unavailable(
            side=side,
            predicted_target=predicted_target,
            side_probability=side_probability,
            market_probability_observed=True,
            reason="selected_book_missing",
            config=config,
            min_edge=min_edge,
        )
    if not _clear_flag(row, f"missing__{prefix}_book_stale"):
        return _unavailable(
            side=side,
            predicted_target=predicted_target,
            side_probability=side_probability,
            market_probability_observed=True,
            reason="selected_book_stale",
            config=config,
            min_edge=min_edge,
        )

    ask = _valid_ask(row.predictors.get(f"{prefix}_best_ask"))
    if ask is None:
        return _unavailable(
            side=side,
            predicted_target=predicted_target,
            side_probability=side_probability,
            market_probability_observed=True,
            reason="selected_ask_unavailable",
            config=config,
            min_edge=min_edge,
        )

    bid = _valid_bid(row.predictors.get(f"{prefix}_best_bid"), ask)
    spread = ask - bid if bid is not None else None
    fee = config.fee_rate * ask * (1.0 - ask)
    raw_edge = side_probability - ask
    cost_adjusted_edge = raw_edge - fee - config.slippage_buffer

    if config.max_spread is not None:
        if spread is None:
            reason = "spread_unavailable"
            trade = False
        elif spread > config.max_spread:
            reason = "spread_too_wide"
            trade = False
        elif min_edge is None:
            reason = "policy_no_trade"
            trade = False
        else:
            trade = cost_adjusted_edge >= min_edge
            reason = "trade" if trade else "edge_below_minimum"
    elif min_edge is None:
        reason = "policy_no_trade"
        trade = False
    else:
        trade = cost_adjusted_edge >= min_edge
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
        slippage_buffer=config.slippage_buffer,
        raw_edge=raw_edge,
        cost_adjusted_edge=cost_adjusted_edge,
        min_edge=min_edge,
    )


def evaluate_edge_policy(
    rows: tuple[SupervisedRow, ...],
    probabilities_up: tuple[float, ...],
    config: EdgeConfig,
    min_edge: float | None,
) -> EdgePolicyMetrics:
    if not rows:
        raise ValueError("rows must not be empty")
    if len(rows) != len(probabilities_up):
        raise ValueError("rows and probabilities must have equal length")

    reasons: Counter[str] = Counter()
    asks: list[float] = []
    spreads: list[float] = []
    raw_edges: list[float] = []
    adjusted_edges: list[float] = []
    fees: list[float] = []
    gross_pnl: list[float] = []
    assumed_cost_pnl: list[float] = []
    observed_market_probability = 0
    executable = 0
    correct = 0

    for row, probability in zip(rows, probabilities_up, strict=True):
        decision = edge_decision(row, probability, config, min_edge)
        reasons[decision.reason] += 1
        observed_market_probability += int(decision.market_probability_observed)
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
        assumed_cost_pnl.append(gross - decision.fee - config.slippage_buffer)

    trade_count = len(gross_pnl)
    prediction_markets = len(rows)
    no_fill = prediction_markets - executable
    abstained_edge = executable - trade_count
    return EdgePolicyMetrics(
        prediction_markets=prediction_markets,
        market_probability_observed_markets=observed_market_probability,
        executable_markets=executable,
        trade_count=trade_count,
        no_fill_markets=no_fill,
        abstained_edge_markets=abstained_edge,
        reason_counts=dict(sorted(reasons.items())),
        trade_coverage=trade_count / prediction_markets,
        average_observed_ask=sum(asks) / len(asks) if asks else None,
        average_observed_spread=sum(spreads) / len(spreads) if spreads else None,
        correct_trades=correct,
        traded_accuracy=correct / trade_count if trade_count else None,
        raw_expected_edge_sum=sum(raw_edges),
        mean_raw_expected_edge=sum(raw_edges) / trade_count if trade_count else None,
        fee_sum=sum(fees),
        slippage_sum=config.slippage_buffer * trade_count,
        cost_adjusted_expected_edge_sum=sum(adjusted_edges),
        mean_cost_adjusted_expected_edge=(
            sum(adjusted_edges) / trade_count if trade_count else None
        ),
        gross_realized_pnl_before_costs=sum(gross_pnl),
        realized_pnl_after_assumed_costs=sum(assumed_cost_pnl),
        mean_realized_pnl_after_assumed_costs=(
            sum(assumed_cost_pnl) / trade_count if trade_count else None
        ),
    )


def select_validation_edge_policy(
    rows: tuple[SupervisedRow, ...],
    probabilities_up: tuple[float, ...],
    config: EdgeConfig,
) -> EdgePolicySelection:
    candidates: list[EdgeThresholdCandidate] = []
    eligible: list[EdgeThresholdCandidate] = []
    for min_edge in config.min_edge_grid:
        metrics = evaluate_edge_policy(rows, probabilities_up, config, min_edge)
        is_eligible = (
            metrics.trade_count >= config.min_validation_trades
            and metrics.realized_pnl_after_assumed_costs > 0
            and metrics.mean_realized_pnl_after_assumed_costs is not None
            and metrics.mean_realized_pnl_after_assumed_costs > 0
        )
        candidate = EdgeThresholdCandidate(
            min_edge=min_edge,
            metrics=metrics,
            eligible=is_eligible,
        )
        candidates.append(candidate)
        if is_eligible:
            eligible.append(candidate)

    if not eligible:
        return EdgePolicySelection(
            policy="no_trade",
            min_edge=None,
            validation_metrics=evaluate_edge_policy(rows, probabilities_up, config, None),
            candidates=tuple(candidates),
        )

    selected = max(
        eligible,
        key=lambda candidate: (
            candidate.metrics.realized_pnl_after_assumed_costs,
            candidate.metrics.mean_realized_pnl_after_assumed_costs,
            candidate.min_edge,
        ),
    )
    return EdgePolicySelection(
        policy="trade_threshold",
        min_edge=selected.min_edge,
        validation_metrics=selected.metrics,
        candidates=tuple(candidates),
    )
