from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class GateBPlanConfig:
    min_initial_train_markets: int = 128
    validation_markets: int = 64
    test_markets: int = 48
    final_holdout_markets: int = 64
    embargo_markets: int = 1

    def __post_init__(self) -> None:
        for name in (
            "min_initial_train_markets",
            "validation_markets",
            "test_markets",
            "final_holdout_markets",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.embargo_markets < 0:
            raise ValueError("embargo_markets must be non-negative")


@dataclass(frozen=True)
class GateBResearchConfig:
    fee_rate: float = 0.07
    slippage_buffer: float = 0.01
    min_edge_grid: tuple[float, ...] = (0.0, 0.015, 0.03, 0.05, 0.075, 0.10, 0.15)
    min_validation_trades: int = 8
    min_train_eligible_markets: int = 24
    min_validation_eligible_markets: int = 8

    def __post_init__(self) -> None:
        if not math.isfinite(self.fee_rate) or self.fee_rate < 0:
            raise ValueError("fee_rate must be finite and non-negative")
        if not math.isfinite(self.slippage_buffer) or self.slippage_buffer < 0:
            raise ValueError("slippage_buffer must be finite and non-negative")
        if not self.min_edge_grid:
            raise ValueError("min_edge_grid must not be empty")
        if tuple(sorted(set(self.min_edge_grid))) != self.min_edge_grid:
            raise ValueError("min_edge_grid must be strictly increasing and unique")
        if any(not math.isfinite(value) or value < 0 for value in self.min_edge_grid):
            raise ValueError("min_edge_grid values must be finite and non-negative")
        if self.min_validation_trades <= 0:
            raise ValueError("min_validation_trades must be positive")
        if self.min_train_eligible_markets <= 0:
            raise ValueError("min_train_eligible_markets must be positive")
        if self.min_validation_eligible_markets <= 0:
            raise ValueError("min_validation_eligible_markets must be positive")
