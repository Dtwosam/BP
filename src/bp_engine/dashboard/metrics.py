from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

from bp_engine.dashboard.models import CalibrationBucket, HorizonPerformance, PerformanceResponse

VERIFIED_HORIZONS = (300, 900)
_BUCKET_COUNT = 10
_BUCKET_WIDTH = Decimal("0.1")


def _as_decimal(value: object, *, field: str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return Decimal(value)
    if isinstance(value, str):
        return Decimal(value)
    raise ValueError(f"{field} must be decimal-compatible")


def _validated_row(row: Mapping[str, object]) -> tuple[int, Decimal, int, bool, Decimal, Decimal]:
    horizon = row.get("horizon_seconds")
    if (
        not isinstance(horizon, int)
        or isinstance(horizon, bool)
        or horizon not in VERIFIED_HORIZONS
    ):
        raise ValueError("horizon must be one of 300 or 900 seconds")

    probability = _as_decimal(row.get("calibrated_probability"), field="probability")
    if probability < 0 or probability > 1:
        raise ValueError("probability must be between 0 and 1")

    target = row.get("official_target")
    if target not in (0, 1):
        raise ValueError("official target must be 0 or 1")

    correct = row.get("correct")
    if not isinstance(correct, bool):
        raise ValueError("correct must be boolean")

    brier = _as_decimal(row.get("calibrated_brier"), field="calibrated_brier")
    log_loss = _as_decimal(row.get("calibrated_log_loss"), field="calibrated_log_loss")
    return horizon, probability, target, correct, brier, log_loss


def _build_buckets(rows: Sequence[Mapping[str, object]]) -> tuple[CalibrationBucket, ...]:
    probabilities: list[list[Decimal]] = [[] for _ in range(_BUCKET_COUNT)]
    targets: list[list[int]] = [[] for _ in range(_BUCKET_COUNT)]

    for row in rows:
        _, probability, target, _, _, _ = _validated_row(row)
        index = min(int(probability * _BUCKET_COUNT), _BUCKET_COUNT - 1)
        probabilities[index].append(probability)
        targets[index].append(target)

    buckets: list[CalibrationBucket] = []
    for index in range(_BUCKET_COUNT):
        lower = Decimal(index) * _BUCKET_WIDTH
        upper = Decimal(index + 1) * _BUCKET_WIDTH
        values = probabilities[index]
        count = len(values)
        if count:
            mean_probability = sum(values, Decimal(0)) / Decimal(count)
            observed_up_frequency = Decimal(sum(targets[index])) / Decimal(count)
        else:
            mean_probability = None
            observed_up_frequency = None
        buckets.append(
            CalibrationBucket(
                lower_bound=lower,
                upper_bound=upper,
                count=count,
                mean_probability=mean_probability,
                observed_up_frequency=observed_up_frequency,
            )
        )
    return tuple(buckets)


def _summarize_horizon(
    horizon: int, rows: Sequence[Mapping[str, object]]
) -> HorizonPerformance:
    validated = [_validated_row(row) for row in rows]
    count = len(validated)
    return HorizonPerformance(
        horizon_seconds=horizon,
        evaluated_count=count,
        accuracy=Decimal(sum(int(item[3]) for item in validated)) / Decimal(count),
        calibrated_brier=sum((item[4] for item in validated), Decimal(0)) / Decimal(count),
        calibrated_log_loss=sum((item[5] for item in validated), Decimal(0)) / Decimal(count),
    )


def build_performance(rows: Sequence[Mapping[str, object]]) -> PerformanceResponse:
    calibration_buckets = _build_buckets(rows)
    if not rows:
        return PerformanceResponse(
            status="pending",
            evaluated_count=0,
            accuracy=None,
            calibrated_brier=None,
            calibrated_log_loss=None,
            horizons=(),
            calibration_buckets=calibration_buckets,
            research_hypothetical_assumed_cost_pnl=None,
        )

    validated = [_validated_row(row) for row in rows]
    count = len(validated)
    horizon_summaries = tuple(
        _summarize_horizon(horizon, [row for row in rows if row["horizon_seconds"] == horizon])
        for horizon in VERIFIED_HORIZONS
        if any(row["horizon_seconds"] == horizon for row in rows)
    )

    pnl_values = [
        _as_decimal(value, field="hypothetical_assumed_cost_pnl")
        for row in rows
        if (value := row.get("hypothetical_assumed_cost_pnl")) is not None
    ]

    return PerformanceResponse(
        status="evaluated",
        evaluated_count=count,
        accuracy=Decimal(sum(int(item[3]) for item in validated)) / Decimal(count),
        calibrated_brier=sum((item[4] for item in validated), Decimal(0)) / Decimal(count),
        calibrated_log_loss=sum((item[5] for item in validated), Decimal(0)) / Decimal(count),
        horizons=horizon_summaries,
        calibration_buckets=calibration_buckets,
        research_hypothetical_assumed_cost_pnl=(
            sum(pnl_values, Decimal(0)) if pnl_values else None
        ),
    )
