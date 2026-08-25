from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

DATASET_VERSION = "supervised-core-v1"
SPLIT_VERSION = "chronological-market-v1"


@dataclass(frozen=True)
class SupervisedRow:
    condition_id: str
    slug: str
    horizon_seconds: int
    market_start_at: datetime
    market_end_at: datetime
    feature_at: datetime
    feature_offset_seconds: int
    predictors: dict[str, float | None]
    target: int
    feature_hash: str
    input_fingerprint: str


@dataclass(frozen=True)
class DatasetSnapshot:
    dataset_version: str
    feature_version: str
    label_version: str
    horizon_seconds: int
    start: datetime
    end: datetime
    rows: tuple[SupervisedRow, ...]
    predictor_names: tuple[str, ...]
    dataset_sha256: str


@dataclass(frozen=True)
class MarketPartition:
    name: str
    condition_ids: tuple[str, ...]
    rows: tuple[SupervisedRow, ...]


@dataclass(frozen=True)
class DatasetSplit:
    split_version: str
    dataset_sha256: str
    train: MarketPartition
    validation: MarketPartition
    test: MarketPartition
    embargo_condition_ids: tuple[str, ...]
    split_sha256: str


@dataclass(frozen=True)
class MetricSummary:
    row_count: int
    market_count: int
    accuracy: float
    balanced_accuracy: float | None
    log_loss: float
    brier_score: float
    ece: float
    calibration: tuple[dict[str, Any], ...]
    confidence_coverage: dict[str, dict[str, float | int]]


@dataclass(frozen=True)
class ModelEvaluation:
    family: str
    config: dict[str, Any]
    validation: MetricSummary
    test: MetricSummary


@dataclass(frozen=True)
class TrainingRunReport:
    run_id: str
    dataset_version: str
    split_version: str
    feature_version: str
    label_version: str
    horizon_seconds: int
    start: datetime
    end: datetime
    dataset_sha256: str
    split_sha256: str
    predictor_names: tuple[str, ...]
    dropped_all_missing: tuple[str, ...]
    model_configs: dict[str, dict[str, Any]]
    validation_champion: str
    best_test_result: str
    boosted_promotion_eligible: bool
    evaluations: dict[str, ModelEvaluation]
    offset_metrics: dict[str, Any]
    gross_execution_diagnostic: dict[str, Any]
    artifacts: tuple[dict[str, Any], ...]
    semantic_sha256: str
    created_at: datetime
