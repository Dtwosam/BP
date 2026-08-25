from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from bp_engine.modeling.models import MetricSummary

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


@dataclass(frozen=True)
class FoldEvaluationReport:
    index: int
    membership_sha256: str
    train_condition_ids: tuple[str, ...]
    validation_condition_ids: tuple[str, ...]
    test_condition_ids: tuple[str, ...]
    selected_offset_seconds: int
    validation_candidates: tuple[Any, ...]
    expected_test_markets: int
    predicted_test_markets: int
    missing_offset_condition_ids: tuple[str, ...]
    prediction_coverage: float
    metrics: MetricSummary
    accuracy_wilson_95: tuple[float, float]
    volatility_threshold: float | None
    execution: dict[str, Any]
    regimes: dict[str, Any]


@dataclass(frozen=True)
class FinalHoldoutReport:
    membership_sha256: str
    train_condition_ids: tuple[str, ...]
    validation_condition_ids: tuple[str, ...]
    holdout_condition_ids: tuple[str, ...]
    selected_offset_seconds: int
    validation_candidates: tuple[Any, ...]
    expected_holdout_markets: int
    predicted_holdout_markets: int
    missing_offset_condition_ids: tuple[str, ...]
    prediction_coverage: float
    metrics: MetricSummary
    accuracy_wilson_95: tuple[float, float]
    volatility_threshold: float | None
    execution: dict[str, Any]
    regimes: dict[str, Any]


@dataclass(frozen=True)
class BacktestReport:
    run_id: str
    backtest_version: str
    source_training_run_id: str
    source_training_semantic_sha256: str
    dataset_version: str
    feature_version: str
    label_version: str
    horizon_seconds: int
    start: datetime
    end: datetime
    dataset_sha256: str
    config: dict[str, Any]
    config_sha256: str
    plan_sha256: str
    fold_membership_sha256: tuple[str, ...]
    folds: tuple[FoldEvaluationReport, ...]
    aggregate_oos_condition_ids: tuple[str, ...]
    aggregate_oos_metrics: MetricSummary
    aggregate_oos_accuracy_wilson_95: tuple[float, float]
    aggregate_oos_execution: dict[str, Any]
    aggregate_oos_regimes: dict[str, Any]
    final_holdout: FinalHoldoutReport
    semantic_sha256: str
    created_at: datetime
