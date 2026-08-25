from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection

from bp_engine.modeling.baselines import MarketPriceBaseline, PriorBaseline
from bp_engine.modeling.models import DATASET_VERSION, SPLIT_VERSION, SupervisedRow
from bp_engine.modeling.repository import ModelTrainingRunRepository
from bp_engine.modeling.split import equal_market_weights

EXPECTED_FEATURE_VERSION = "core-v1"
EXPECTED_LABEL_VERSION = "official-outcome-v1"
EXPECTED_CHAMPION = "market_price"
EXPECTED_MARKET_PRICE_CONFIG: dict[str, Any] = {
    "predictor": "pm_up_price",
    "missing_fallback": "training_prior",
    "clip_epsilon": 1e-6,
}


class SourceTrainingRunNotFound(LookupError):
    """Raised when a requested immutable Phase 7 training run is absent."""


class ModelSpecIntegrityError(ValueError):
    """Raised when a source training run violates the Phase 8 model contract."""


@dataclass(frozen=True)
class ModelSpec:
    run_id: str
    semantic_sha256: str
    horizon_seconds: int
    dataset_version: str
    split_version: str
    feature_version: str
    label_version: str
    validation_champion: str
    market_price_config: dict[str, Any]


def load_model_spec(connection: Connection, run_id: str) -> ModelSpec:
    stored = ModelTrainingRunRepository().get(connection, run_id)
    if stored is None:
        raise SourceTrainingRunNotFound(f"source training run not found: {run_id}")

    expected_versions = {
        "dataset_version": DATASET_VERSION,
        "split_version": SPLIT_VERSION,
        "feature_version": EXPECTED_FEATURE_VERSION,
        "label_version": EXPECTED_LABEL_VERSION,
    }
    for field, expected in expected_versions.items():
        if stored[field] != expected:
            raise ModelSpecIntegrityError(
                f"source training run {run_id} has unexpected {field}={stored[field]!r}"
            )
    if stored["validation_champion"] != EXPECTED_CHAMPION:
        raise ModelSpecIntegrityError(
            f"source training run {run_id} validation champion must be market_price"
        )

    model_configs = stored["model_configs"]
    if not isinstance(model_configs, dict):
        raise ModelSpecIntegrityError(
            f"source training run {run_id} model_configs must be a mapping"
        )
    market_price_config = model_configs.get("market_price")
    if market_price_config != EXPECTED_MARKET_PRICE_CONFIG:
        raise ModelSpecIntegrityError(
            f"source training run {run_id} has unexpected market_price config"
        )

    horizon_seconds = int(stored["horizon_seconds"])
    semantic_sha256 = str(stored["semantic_sha256"])
    if horizon_seconds <= 0:
        raise ModelSpecIntegrityError(
            f"source training run {run_id} horizon_seconds must be positive"
        )
    if len(semantic_sha256) != 64:
        raise ModelSpecIntegrityError(
            f"source training run {run_id} semantic_sha256 must be SHA-256"
        )

    return ModelSpec(
        run_id=str(stored["run_id"]),
        semantic_sha256=semantic_sha256,
        horizon_seconds=horizon_seconds,
        dataset_version=str(stored["dataset_version"]),
        split_version=str(stored["split_version"]),
        feature_version=str(stored["feature_version"]),
        label_version=str(stored["label_version"]),
        validation_champion=str(stored["validation_champion"]),
        market_price_config=dict(market_price_config),
    )


class MarketPriceFoldPredictor:
    """Phase 7 market-price baseline refitted only for training-prior fallback."""

    def __init__(self) -> None:
        self._baseline: MarketPriceBaseline | None = None

    def fit(self, rows: tuple[SupervisedRow, ...]) -> None:
        prior = PriorBaseline()
        prior.fit(rows, equal_market_weights(rows))
        assert prior.probability is not None
        self._baseline = MarketPriceBaseline(prior.probability)

    def predict(self, rows: tuple[SupervisedRow, ...]) -> tuple[float, ...]:
        if self._baseline is None:
            raise RuntimeError("predictor must be fitted before prediction")
        return self._baseline.predict_proba(rows)

    @staticmethod
    def observed_price_coverage(rows: tuple[SupervisedRow, ...]) -> float:
        if not rows:
            return 0.0
        observed = sum(row.predictors.get("pm_up_price") is not None for row in rows)
        return observed / len(rows)
