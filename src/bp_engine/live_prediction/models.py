from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

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


@dataclass(frozen=True)
class LivePrediction:
    prediction_id: str
    semantic_sha256: str
    prediction_version: str
    live_input_version: str
    condition_id: str
    slug: str
    horizon_seconds: int
    market_start_at: datetime
    market_end_at: datetime
    scheduled_at: datetime
    recorded_at: datetime
    lateness_ms: int
    up_token_id: str
    down_token_id: str
    source_calibration_run_id: str
    source_calibration_semantic_sha256: str
    source_backtest_run_id: str
    source_backtest_semantic_sha256: str
    source_training_run_id: str
    source_training_semantic_sha256: str
    calibration_version: str
    edge_policy_version: str
    source_feature_version: str
    source_label_version: str
    selected_offset_seconds: int
    policy_sha256: str
    calibration_fit: Mapping[str, Any]
    calibration_fit_sha256: str
    edge_config: Mapping[str, Any]
    edge_config_sha256: str
    edge_policy: str
    min_edge: float | None
    training_prior: float
    raw_probability: float
    calibrated_probability: float
    predicted_target: int
    predicted_side: str
    market_probability_observed: bool
    market_probability: float | None
    market_probability_observed_at: datetime | None
    market_probability_downloaded_at: datetime
    market_probability_source: str
    market_probability_dataset: str
    market_probability_request_params: Mapping[str, Any]
    market_probability_response_sha256: str
    up_best_bid: float | None
    up_best_ask: float | None
    up_book_cutoff_at: datetime | None
    up_book_fresh: bool
    down_best_bid: float | None
    down_best_ask: float | None
    down_book_cutoff_at: datetime | None
    down_book_fresh: bool
    selected_side: str
    executable: bool
    trade: bool
    decision_reason: str
    selected_ask: float | None
    selected_bid: float | None
    selected_spread: float | None
    fee: float
    slippage_buffer: float
    raw_edge: float | None
    cost_adjusted_edge: float | None
    decision_min_edge: float | None
    edge_decision: Mapping[str, Any]
    input_fingerprint: str


@dataclass(frozen=True)
class LivePredictionEvaluation:
    prediction_id: str
    label_version: str
    official_outcome: str
    official_target: int
    label_source: str
    label_source_snapshot_sha256: str
    label_source_observed_at: datetime
    evaluated_at: datetime
    correct: bool
    raw_log_loss: float
    raw_brier: float
    calibrated_log_loss: float
    calibrated_brier: float
    hypothetical_gross_pnl: float | None
    hypothetical_assumed_cost_pnl: float | None
    semantic_sha256: str
