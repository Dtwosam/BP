from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict


class DashboardModel(BaseModel):
    model_config = ConfigDict(frozen=True)


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
