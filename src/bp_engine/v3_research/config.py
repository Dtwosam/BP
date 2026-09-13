from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class V3GateBConfig:
    research_plan_version: str
    dataset_version: str
    feature_version: str
    label_version: str
    horizon_seconds: int
    feature_offsets_seconds: tuple[int, ...]
    epoch_start: datetime
    epoch_end: datetime
    train_duration: timedelta
    validation_duration: timedelta
    test_duration: timedelta
    step_duration: timedelta
    final_holdout_duration: timedelta
    embargo_markets: int
    min_train_markets: int
    min_validation_markets: int
    min_test_markets: int
    min_final_holdout_markets: int
    ordinary_fold_count: int

    def __post_init__(self) -> None:
        if self.epoch_start.tzinfo is None or self.epoch_end.tzinfo is None:
            raise ValueError("epoch bounds must be timezone-aware")
        if self.epoch_end <= self.epoch_start:
            raise ValueError("epoch_end must be after epoch_start")
        if self.horizon_seconds <= 0:
            raise ValueError("horizon_seconds must be positive")
        if not self.feature_offsets_seconds:
            raise ValueError("feature_offsets_seconds must be non-empty")
        if tuple(sorted(set(self.feature_offsets_seconds))) != self.feature_offsets_seconds:
            raise ValueError("feature_offsets_seconds must be sorted and unique")
        if any(offset <= 0 or offset >= self.horizon_seconds for offset in self.feature_offsets_seconds):
            raise ValueError("feature offsets must be within the market horizon")
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
            "min_final_holdout_markets",
            "ordinary_fold_count",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")

        ordinary_span = self.epoch_end - self.epoch_start - self.final_holdout_duration
        first_fold_span = self.train_duration + self.validation_duration + self.test_duration
        if ordinary_span < first_fold_span:
            raise ValueError("epoch is too short for one ordinary fold")
        calculated_fold_count = (
            (ordinary_span - first_fold_span) // self.step_duration
        ) + 1
        if calculated_fold_count != self.ordinary_fold_count:
            raise ValueError("ordinary_fold_count does not match frozen geometry")


FROZEN_V3_GATE_B_CONFIG = V3GateBConfig(
    research_plan_version="v3-gate-b-preregister-v1",
    dataset_version="supervised-core-v3-btc-native-v1",
    feature_version="core-v3-btc-native",
    label_version="official-outcome-v1",
    horizon_seconds=300,
    feature_offsets_seconds=(60, 120, 180, 240),
    epoch_start=datetime(2026, 9, 13, 13, 45, tzinfo=UTC),
    epoch_end=datetime(2026, 9, 16, 13, 45, tzinfo=UTC),
    train_duration=timedelta(hours=24),
    validation_duration=timedelta(hours=6),
    test_duration=timedelta(hours=6),
    step_duration=timedelta(hours=6),
    final_holdout_duration=timedelta(hours=12),
    embargo_markets=1,
    min_train_markets=240,
    min_validation_markets=60,
    min_test_markets=60,
    min_final_holdout_markets=120,
    ordinary_fold_count=5,
)
