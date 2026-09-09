from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class GateBPlanConfig:
    train_duration: timedelta = timedelta(hours=8)
    validation_duration: timedelta = timedelta(hours=2)
    test_duration: timedelta = timedelta(hours=2)
    step_duration: timedelta = timedelta(hours=2)
    final_holdout_duration: timedelta = timedelta(hours=2)
    embargo_markets: int = 1
    min_train_markets: int = 24
    min_validation_markets: int = 6
    min_test_markets: int = 6

    def __post_init__(self) -> None:
        for name in (
            "train_duration",
            "validation_duration",
            "test_duration",
            "step_duration",
            "final_holdout_duration",
        ):
            if getattr(self, name) <= timedelta(0):
                raise ValueError(f"{name} must be positive")
        if self.step_duration < self.test_duration:
            raise ValueError("step_duration must be at least test_duration")
        if self.embargo_markets < 0:
            raise ValueError("embargo_markets must be non-negative")
        for name in (
            "min_train_markets",
            "min_validation_markets",
            "min_test_markets",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")


@dataclass(frozen=True)
class GateBResearchConfig:
    fee_rate: float = 0.07
    slippage_buffer: float = 0.01
    min_edge_grid: tuple[float, ...] = (
        0.0,
        0.01,
        0.02,
        0.03,
        0.05,
        0.075,
        0.10,
        0.15,
    )
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
