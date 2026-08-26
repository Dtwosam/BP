from __future__ import annotations

from dataclasses import dataclass

from bp_engine.calibration.models import CalibrationFit, EdgeConfig

LIVE_PREDICTION_VERSION = "live-prediction-v1"
LIVE_INPUT_VERSION = "phase10-live-market-input-v1"


@dataclass(frozen=True)
class LivePolicySpec:
    source_calibration_run_id: str
    source_calibration_semantic_sha256: str
    source_backtest_run_id: str
    source_backtest_semantic_sha256: str
    source_training_run_id: str
    source_training_semantic_sha256: str
    calibration_version: str
    edge_policy_version: str
    source_feature_version: str
    label_version: str
    horizon_seconds: int
    selected_offset_seconds: int
    calibration_fit: CalibrationFit
    edge_config: EdgeConfig
    edge_policy: str
    min_edge: float | None
    training_prior: float
    policy_sha256: str
