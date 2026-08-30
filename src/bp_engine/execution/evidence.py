from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import numpy as np
from sqlalchemy import select
from sqlalchemy.engine import Engine

from bp_engine.storage import schema


@dataclass(frozen=True)
class ProspectivePaperEvidenceReport:
    evaluated_prediction_count: int
    settled_trade_count: int
    total_realized_pnl: Decimal
    mean_realized_pnl: float | None
    pnl_mean_ci_lower: float | None
    pnl_mean_ci_upper: float | None
    pnl_bootstrap_resamples: int
    accuracy: float | None
    brier_score: float | None
    log_loss: float | None
    mean_calibrated_probability: float | None
    observed_up_rate: float | None
    aggregate_calibration_gap: float | None
    reconciliation_status: str
    reconciliation_violation_count: int


@dataclass(frozen=True)
class ProspectivePaperEvidenceInputs:
    predictions: tuple[dict[str, object], ...]
    evaluations: tuple[dict[str, object], ...]
    settled_trades: tuple[dict[str, object], ...]


class PostgresProspectivePaperEvidenceReader:
    """Read immutable paper evidence whose source prediction/order is prospective."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def load(self, *, since: datetime) -> ProspectivePaperEvidenceInputs:
        if since.tzinfo is None or since.utcoffset() is None:
            raise ValueError("since must be timezone-aware")
        since_utc = since.astimezone(UTC)

        prediction_query = (
            select(
                schema.live_predictions.c.prediction_id,
                schema.live_predictions.c.condition_id,
                schema.live_predictions.c.horizon_seconds,
                schema.live_predictions.c.calibrated_probability,
            )
            .where(schema.live_predictions.c.recorded_at >= since_utc)
            .order_by(
                schema.live_predictions.c.recorded_at.asc(),
                schema.live_predictions.c.prediction_id.asc(),
            )
        )
        evaluation_query = (
            select(
                schema.live_prediction_evaluations.c.prediction_id,
                schema.live_prediction_evaluations.c.official_target,
            )
            .join(
                schema.live_predictions,
                schema.live_predictions.c.prediction_id
                == schema.live_prediction_evaluations.c.prediction_id,
            )
            .where(schema.live_predictions.c.recorded_at >= since_utc)
            .order_by(
                schema.live_predictions.c.recorded_at.asc(),
                schema.live_prediction_evaluations.c.prediction_id.asc(),
            )
        )
        settlement_query = (
            select(
                schema.paper_settlements.c.paper_order_id,
                schema.paper_orders.c.condition_id,
                schema.paper_settlements.c.realized_pnl,
            )
            .join(
                schema.paper_orders,
                schema.paper_orders.c.paper_order_id
                == schema.paper_settlements.c.paper_order_id,
            )
            .where(schema.paper_orders.c.submitted_at >= since_utc)
            .order_by(
                schema.paper_orders.c.submitted_at.asc(),
                schema.paper_settlements.c.paper_order_id.asc(),
            )
        )

        with self.engine.connect() as connection:
            predictions = tuple(
                dict(row) for row in connection.execute(prediction_query).mappings().all()
            )
            evaluations = tuple(
                dict(row) for row in connection.execute(evaluation_query).mappings().all()
            )
            settled_trades = tuple(
                dict(row) for row in connection.execute(settlement_query).mappings().all()
            )

        return ProspectivePaperEvidenceInputs(
            predictions=predictions,
            evaluations=evaluations,
            settled_trades=settled_trades,
        )


def _probability(value: object) -> float:
    probability = float(value)
    if not math.isfinite(probability) or probability < 0.0 or probability > 1.0:
        raise ValueError("calibrated probability must be finite and within [0, 1]")
    return probability


def _target(value: object) -> int:
    target = int(value)
    if target not in {0, 1}:
        raise ValueError("official target must be 0 or 1")
    return target


def _pnl(value: object) -> Decimal:
    pnl = value if isinstance(value, Decimal) else Decimal(str(value))
    if not pnl.is_finite():
        raise ValueError("realized P&L must be finite")
    return pnl


def _bootstrap_mean_interval(
    values: Sequence[Decimal],
    *,
    seed: int,
    resamples: int,
) -> tuple[float, float, float] | None:
    if not values:
        return None
    sample = np.asarray([float(value) for value in values], dtype=float)
    generator = np.random.Generator(np.random.PCG64(seed))
    means = np.empty(resamples, dtype=float)
    for index in range(resamples):
        sampled_indices = generator.integers(0, len(sample), size=len(sample))
        means[index] = float(sample[sampled_indices].mean())
    lower, upper = np.percentile(means, (2.5, 97.5))
    return float(sample.mean()), float(lower), float(upper)


def summarize_prospective_paper_evidence(
    *,
    predictions: Sequence[Mapping[str, object]],
    evaluations: Sequence[Mapping[str, object]],
    settled_trades: Sequence[Mapping[str, object]],
    reconciliation: Mapping[str, object],
    seed: int,
    resamples: int = 10_000,
) -> ProspectivePaperEvidenceReport:
    """Summarize immutable prospective paper evidence without declaring gate sufficiency."""

    if resamples <= 0:
        raise ValueError("resamples must be positive")

    prediction_by_id: dict[str, Mapping[str, object]] = {}
    for prediction in predictions:
        prediction_id = str(prediction["prediction_id"])
        if not prediction_id or prediction_id in prediction_by_id:
            raise ValueError("prediction ids must be non-empty and unique")
        prediction_by_id[prediction_id] = prediction

    evaluated: list[tuple[float, int]] = []
    seen_evaluations: set[str] = set()
    for evaluation in evaluations:
        prediction_id = str(evaluation["prediction_id"])
        if prediction_id in seen_evaluations:
            raise ValueError("evaluations must contain at most one row per prediction")
        seen_evaluations.add(prediction_id)
        prediction = prediction_by_id.get(prediction_id)
        if prediction is None:
            continue
        evaluated.append(
            (
                _probability(prediction["calibrated_probability"]),
                _target(evaluation["official_target"]),
            )
        )

    pnl_values = [_pnl(trade["realized_pnl"]) for trade in settled_trades]
    total_realized_pnl = sum(pnl_values, Decimal("0"))
    pnl_interval = _bootstrap_mean_interval(
        pnl_values,
        seed=seed,
        resamples=resamples,
    )

    if evaluated:
        probabilities = [probability for probability, _ in evaluated]
        targets = [target for _, target in evaluated]
        accuracy = sum(
            int((probability >= 0.5) == bool(target))
            for probability, target in evaluated
        ) / len(evaluated)
        brier_score = sum(
            (probability - target) ** 2 for probability, target in evaluated
        ) / len(evaluated)
        epsilon = 1e-15
        log_loss = -sum(
            target * math.log(min(max(probability, epsilon), 1.0 - epsilon))
            + (1 - target)
            * math.log(min(max(1.0 - probability, epsilon), 1.0 - epsilon))
            for probability, target in evaluated
        ) / len(evaluated)
        mean_probability = sum(probabilities) / len(probabilities)
        observed_up_rate = sum(targets) / len(targets)
        calibration_gap = abs(mean_probability - observed_up_rate)
    else:
        accuracy = None
        brier_score = None
        log_loss = None
        mean_probability = None
        observed_up_rate = None
        calibration_gap = None

    mean_pnl, pnl_lower, pnl_upper = pnl_interval or (None, None, None)
    reconciliation_status = str(reconciliation.get("status", "UNKNOWN"))
    reconciliation_violation_count = int(reconciliation.get("violation_count", 0))

    return ProspectivePaperEvidenceReport(
        evaluated_prediction_count=len(evaluated),
        settled_trade_count=len(pnl_values),
        total_realized_pnl=total_realized_pnl,
        mean_realized_pnl=mean_pnl,
        pnl_mean_ci_lower=pnl_lower,
        pnl_mean_ci_upper=pnl_upper,
        pnl_bootstrap_resamples=resamples,
        accuracy=accuracy,
        brier_score=brier_score,
        log_loss=log_loss,
        mean_calibrated_probability=mean_probability,
        observed_up_rate=observed_up_rate,
        aggregate_calibration_gap=calibration_gap,
        reconciliation_status=reconciliation_status,
        reconciliation_violation_count=reconciliation_violation_count,
    )