from __future__ import annotations

import math
from dataclasses import dataclass

from bp_engine.modeling.models import MetricSummary

CALIBRATION_VERSION = "platt-or-identity-v1"
EDGE_POLICY_VERSION = "selected-ask-edge-v1"


@dataclass(frozen=True)
class CalibrationFit:
    method: str
    intercept: float | None
    coefficient: float | None


@dataclass(frozen=True)
class CalibrationCandidate:
    method: str
    fit: CalibrationFit
    validation_metrics: MetricSummary


@dataclass(frozen=True)
class CalibrationSelection:
    method: str
    fit: CalibrationFit
    validation_metrics: MetricSummary
    candidates: tuple[CalibrationCandidate, ...]


@dataclass(frozen=True)
class EdgeConfig:
    fee_rate: float
    slippage_buffer: float
    min_edge_grid: tuple[float, ...]
    min_validation_trades: int
    max_spread: float | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.fee_rate) or self.fee_rate < 0:
            raise ValueError("fee_rate must be finite and non-negative")
        if not math.isfinite(self.slippage_buffer) or self.slippage_buffer < 0:
            raise ValueError("slippage_buffer must be finite and non-negative")
        if not self.min_edge_grid:
            raise ValueError("min_edge_grid must not be empty")
        if any(not math.isfinite(value) or value < 0 for value in self.min_edge_grid):
            raise ValueError("min_edge_grid values must be finite and non-negative")
        if tuple(sorted(set(self.min_edge_grid))) != self.min_edge_grid:
            raise ValueError("min_edge_grid must be strictly increasing and unique")
        if self.min_validation_trades <= 0:
            raise ValueError("min_validation_trades must be positive")
        if self.max_spread is not None and (
            not math.isfinite(self.max_spread) or not 0 < self.max_spread <= 1
        ):
            raise ValueError("max_spread must be within (0, 1]")


@dataclass(frozen=True)
class EdgeDecision:
    side: str
    predicted_target: int
    side_probability: float
    market_probability_observed: bool
    executable: bool
    trade: bool
    reason: str
    ask: float | None
    bid: float | None
    spread: float | None
    fee: float
    slippage_buffer: float
    raw_edge: float | None
    cost_adjusted_edge: float | None
    min_edge: float | None


@dataclass(frozen=True)
class EdgePolicyMetrics:
    prediction_markets: int
    market_probability_observed_markets: int
    executable_markets: int
    trade_count: int
    no_fill_markets: int
    abstained_edge_markets: int
    reason_counts: dict[str, int]
    trade_coverage: float
    average_observed_ask: float | None
    average_observed_spread: float | None
    correct_trades: int
    traded_accuracy: float | None
    raw_expected_edge_sum: float
    mean_raw_expected_edge: float | None
    fee_sum: float
    slippage_sum: float
    cost_adjusted_expected_edge_sum: float
    mean_cost_adjusted_expected_edge: float | None
    gross_realized_pnl_before_costs: float
    realized_pnl_after_assumed_costs: float
    mean_realized_pnl_after_assumed_costs: float | None


@dataclass(frozen=True)
class EdgeThresholdCandidate:
    min_edge: float
    metrics: EdgePolicyMetrics
    eligible: bool


@dataclass(frozen=True)
class EdgePolicySelection:
    policy: str
    min_edge: float | None
    validation_metrics: EdgePolicyMetrics
    candidates: tuple[EdgeThresholdCandidate, ...]
