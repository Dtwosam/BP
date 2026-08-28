from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict


class DashboardModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class HealthResponse(DashboardModel):
    status: Literal["ok"] = "ok"
    database_status: Literal["ok"]
    generated_at: datetime


class OverviewResponse(DashboardModel):
    generated_at: datetime
    mode: Literal["research"]
    live_trading_enabled: Literal[False]
    verified_horizons_seconds: tuple[int, int]
    database_status: Literal["ok"]
    active_market_count: int
    active_market_count_5m: int
    active_market_count_15m: int
    recent_prediction_count: int
    evaluated_prediction_count: int
    pending_prediction_count: int
    feed_count: int
    unhealthy_feed_count: int
    paper_pnl_status: Literal["unavailable_until_phase_12"] = "unavailable_until_phase_12"


class CalibrationBucket(DashboardModel):
    lower_bound: Decimal
    upper_bound: Decimal
    count: int
    mean_probability: Decimal | None
    observed_up_frequency: Decimal | None


class HorizonPerformance(DashboardModel):
    horizon_seconds: int
    evaluated_count: int
    accuracy: Decimal
    calibrated_brier: Decimal
    calibrated_log_loss: Decimal


class PerformanceResponse(DashboardModel):
    status: Literal["pending", "evaluated"]
    evaluated_count: int
    accuracy: Decimal | None
    calibrated_brier: Decimal | None
    calibrated_log_loss: Decimal | None
    horizons: tuple[HorizonPerformance, ...]
    calibration_buckets: tuple[CalibrationBucket, ...]
    research_hypothetical_assumed_cost_pnl: Decimal | None
    paper_pnl_status: Literal["unavailable_until_phase_12"] = "unavailable_until_phase_12"
