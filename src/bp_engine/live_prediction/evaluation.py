from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, select

from bp_engine.calibration.calibrators import clip_probability
from bp_engine.features.hashing import canonical_hash
from bp_engine.labels.models import MarketLabel
from bp_engine.live_prediction.models import LivePrediction, LivePredictionEvaluation
from bp_engine.live_prediction.repository import LivePredictionEvaluationRepository
from bp_engine.storage.schema import live_predictions, market_labels

OFFICIAL_LABEL_VERSION = "official-outcome-v1"


class EvaluationIntegrityError(ValueError):
    """Raised when a prediction and official label cannot be safely joined."""


@dataclass(frozen=True)
class EvaluationAppendResult:
    created: int
    existing: int


def _aware_utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise EvaluationIntegrityError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _stored_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _float(value: Any, name: str) -> float:
    numeric = float(value)
    if not math.isfinite(numeric):
        raise EvaluationIntegrityError(f"{name} must be finite")
    return numeric


def _row_metrics(probability: float, target: int) -> tuple[float, float]:
    clipped = clip_probability(probability)
    likelihood = clipped if target == 1 else 1.0 - clipped
    log_loss = -math.log(likelihood)
    brier = (clipped - float(target)) ** 2
    return log_loss, brier


def _validate_identity(prediction: LivePrediction, label: MarketLabel) -> None:
    if prediction.condition_id != label.condition_id:
        raise EvaluationIntegrityError("condition_id mismatch between prediction and label")
    if prediction.slug != label.slug:
        raise EvaluationIntegrityError("slug mismatch between prediction and label")
    if prediction.horizon_seconds != label.horizon_seconds:
        raise EvaluationIntegrityError("horizon_seconds mismatch between prediction and label")
    if prediction.source_label_version != label.label_version:
        raise EvaluationIntegrityError("label_version mismatch between prediction and label")
    if label.label_version != OFFICIAL_LABEL_VERSION:
        raise EvaluationIntegrityError(
            f"label_version must be {OFFICIAL_LABEL_VERSION}"
        )

    prediction_start = _aware_utc(prediction.market_start_at, "prediction.market_start_at")
    prediction_end = _aware_utc(prediction.market_end_at, "prediction.market_end_at")
    label_start = _aware_utc(label.market_start_at, "label.market_start_at")
    label_end = _aware_utc(label.market_end_at, "label.market_end_at")
    recorded_at = _aware_utc(prediction.recorded_at, "prediction.recorded_at")
    source_observed_at = _aware_utc(label.source_observed_at, "label.source_observed_at")

    if prediction_start != label_start:
        raise EvaluationIntegrityError("market_start_at mismatch between prediction and label")
    if prediction_end != label_end:
        raise EvaluationIntegrityError("market_end_at mismatch between prediction and label")
    if source_observed_at < prediction_end:
        raise EvaluationIntegrityError("label source_observed_at must be at or after market_end")
    if recorded_at >= source_observed_at:
        raise EvaluationIntegrityError(
            "prediction recorded_at must be before label source_observed_at"
        )
    if recorded_at >= prediction_end:
        raise EvaluationIntegrityError("prediction recorded_at must be before market_end")


def evaluate_prediction(
    prediction: LivePrediction,
    label: MarketLabel,
    *,
    evaluated_at: datetime,
) -> LivePredictionEvaluation:
    _validate_identity(prediction, label)
    evaluated = _aware_utc(evaluated_at, "evaluated_at")
    source_observed = _aware_utc(label.source_observed_at, "label.source_observed_at")
    if evaluated < source_observed:
        raise EvaluationIntegrityError("evaluated_at must not precede label source_observed_at")

    if label.official_outcome not in {"Up", "Down"}:
        raise EvaluationIntegrityError("official_outcome must be Up or Down")
    official_target = 1 if label.official_outcome == "Up" else 0
    correct = prediction.predicted_target == official_target

    raw_log_loss, raw_brier = _row_metrics(
        _float(prediction.raw_probability, "raw_probability"),
        official_target,
    )
    calibrated_log_loss, calibrated_brier = _row_metrics(
        _float(prediction.calibrated_probability, "calibrated_probability"),
        official_target,
    )

    hypothetical_gross_pnl: float | None = None
    hypothetical_assumed_cost_pnl: float | None = None
    if prediction.trade:
        if prediction.selected_ask is None:
            raise EvaluationIntegrityError("trade prediction requires selected_ask")
        ask = _float(prediction.selected_ask, "selected_ask")
        if not 0.0 < ask <= 1.0:
            raise EvaluationIntegrityError("selected_ask must be within (0, 1]")
        fee = _float(prediction.fee, "fee")
        slippage = _float(prediction.slippage_buffer, "slippage_buffer")
        payout = 1.0 if correct else 0.0
        hypothetical_gross_pnl = payout - ask
        hypothetical_assumed_cost_pnl = hypothetical_gross_pnl - fee - slippage

    semantic_values = {
        "prediction_id": prediction.prediction_id,
        "label_version": label.label_version,
        "official_outcome": label.official_outcome,
        "official_target": official_target,
        "label_source": label.label_source,
        "label_source_snapshot_sha256": label.source_snapshot_sha256,
        "label_source_observed_at": source_observed,
        "evaluated_at": evaluated,
        "correct": correct,
        "raw_log_loss": raw_log_loss,
        "raw_brier": raw_brier,
        "calibrated_log_loss": calibrated_log_loss,
        "calibrated_brier": calibrated_brier,
        "hypothetical_gross_pnl": hypothetical_gross_pnl,
        "hypothetical_assumed_cost_pnl": hypothetical_assumed_cost_pnl,
    }
    return LivePredictionEvaluation(
        prediction_id=prediction.prediction_id,
        label_version=label.label_version,
        official_outcome=label.official_outcome,
        official_target=official_target,
        label_source=label.label_source,
        label_source_snapshot_sha256=label.source_snapshot_sha256,
        label_source_observed_at=source_observed,
        evaluated_at=evaluated,
        correct=correct,
        raw_log_loss=raw_log_loss,
        raw_brier=raw_brier,
        calibrated_log_loss=calibrated_log_loss,
        calibrated_brier=calibrated_brier,
        hypothetical_gross_pnl=hypothetical_gross_pnl,
        hypothetical_assumed_cost_pnl=hypothetical_assumed_cost_pnl,
        semantic_sha256=canonical_hash(semantic_values),
    )


def _prediction_from_row(row: Any) -> LivePrediction:
    values = {
        name: row[name]
        for name in LivePrediction.__dataclass_fields__
    }
    for name in (
        "market_start_at",
        "market_end_at",
        "scheduled_at",
        "recorded_at",
        "market_probability_downloaded_at",
        "market_probability_observed_at",
        "up_book_cutoff_at",
        "down_book_cutoff_at",
    ):
        value = values[name]
        if value is not None:
            values[name] = _stored_utc(value)
    for name, value in tuple(values.items()):
        if isinstance(value, Decimal):
            values[name] = float(value)
    return LivePrediction(**values)


def _label_from_row(row: Any) -> MarketLabel:
    values = {
        name: row[name]
        for name in MarketLabel.__dataclass_fields__
    }
    for name in (
        "market_start_at",
        "market_end_at",
        "source_observed_at",
        "generated_at",
    ):
        values[name] = _stored_utc(values[name])
    return MarketLabel(**values)


def append_available_evaluations(
    connection: Connection,
    *,
    evaluated_at: datetime,
) -> EvaluationAppendResult:
    evaluated = _aware_utc(evaluated_at, "evaluated_at")
    repository = LivePredictionEvaluationRepository()
    created = 0
    existing_count = 0

    prediction_rows = connection.execute(select(live_predictions)).mappings().all()
    for prediction_row in prediction_rows:
        prediction = _prediction_from_row(prediction_row)
        if prediction.source_label_version != OFFICIAL_LABEL_VERSION:
            continue
        label_row = connection.execute(
            select(market_labels).where(
                market_labels.c.condition_id == prediction.condition_id,
                market_labels.c.label_version == prediction.source_label_version,
            )
        ).mappings().one_or_none()
        if label_row is None:
            continue
        label = _label_from_row(label_row)

        stored_evaluation = repository.get(
            connection,
            prediction_id=prediction.prediction_id,
            label_version=label.label_version,
        )
        effective_evaluated_at = evaluated
        if stored_evaluation is not None:
            effective_evaluated_at = _stored_utc(stored_evaluation["evaluated_at"])

        evaluation = evaluate_prediction(
            prediction,
            label,
            evaluated_at=effective_evaluated_at,
        )
        result = repository.store(connection, evaluation)
        created += int(result.created)
        existing_count += int(result.existing)

    return EvaluationAppendResult(created=created, existing=existing_count)
