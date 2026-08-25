from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

BACKTEST_VERSION = "walk-forward-v1"
MODEL_SPEC_VERSION = "phase7-market-price-v1"


@dataclass(frozen=True)
class WalkForwardConfig:
    train_duration: timedelta
    validation_duration: timedelta
    test_duration: timedelta
    step_duration: timedelta
    final_holdout_duration: timedelta
    embargo_markets: int = 1
    min_train_markets: int = 24
    min_validation_markets: int = 6
    min_test_markets: int = 6
    min_market_price_coverage: float = 0.80
    min_prediction_coverage: float = 0.90

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
        for name in ("min_market_price_coverage", "min_prediction_coverage"):
            value = float(getattr(self, name))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be within [0, 1]")


@dataclass(frozen=True)
class MarketRecord:
    condition_id: str
    slug: str
    horizon_seconds: int
    market_start_at: datetime
    market_end_at: datetime
    target: int


@dataclass(frozen=True)
class FoldPartition:
    name: str
    start: datetime
    end: datetime
    condition_ids: tuple[str, ...]


@dataclass(frozen=True)
class WalkForwardFold:
    index: int
    train: FoldPartition
    validation: FoldPartition
    test: FoldPartition
    purged_condition_ids: tuple[str, ...]
    embargo_condition_ids: tuple[str, ...]
    membership_sha256: str


@dataclass(frozen=True)
class WalkForwardPlan:
    folds: tuple[WalkForwardFold, ...]
    final_train: FoldPartition
    final_validation: FoldPartition
    final_holdout: FoldPartition
    final_purged_condition_ids: tuple[str, ...]
    final_embargo_condition_ids: tuple[str, ...]
    plan_sha256: str
