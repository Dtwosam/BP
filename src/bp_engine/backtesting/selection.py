from __future__ import annotations

from dataclasses import dataclass

from bp_engine.backtesting.predictor import MarketPriceFoldPredictor
from bp_engine.modeling.metrics import evaluate_probabilities
from bp_engine.modeling.models import MetricSummary, SupervisedRow
from bp_engine.modeling.split import equal_market_weights


@dataclass(frozen=True)
class OffsetCandidateReport:
    offset_seconds: int
    market_count: int
    observed_market_price_count: int
    fallback_count: int
    observed_market_price_coverage: float
    metrics: MetricSummary


@dataclass(frozen=True)
class OffsetSelection:
    selected_offset_seconds: int
    candidates: tuple[OffsetCandidateReport, ...]


def rows_at_offset(
    rows: tuple[SupervisedRow, ...], offset_seconds: int
) -> tuple[SupervisedRow, ...]:
    if offset_seconds <= 0:
        raise ValueError("offset_seconds must be positive")
    selected = tuple(row for row in rows if row.feature_offset_seconds == offset_seconds)
    seen: set[str] = set()
    for row in selected:
        if row.condition_id in seen:
            raise ValueError(
                f"duplicate condition at offset {offset_seconds}: {row.condition_id}"
            )
        seen.add(row.condition_id)
    return selected


def select_validation_offset(
    train_rows: tuple[SupervisedRow, ...],
    validation_rows: tuple[SupervisedRow, ...],
    *,
    min_market_price_coverage: float,
    min_validation_markets: int,
) -> OffsetSelection:
    if not train_rows:
        raise ValueError("train_rows must not be empty")
    if not validation_rows:
        raise ValueError("validation_rows must not be empty")
    if not 0.0 <= min_market_price_coverage <= 1.0:
        raise ValueError("min_market_price_coverage must be within [0, 1]")
    if min_validation_markets <= 0:
        raise ValueError("min_validation_markets must be positive")

    predictor = MarketPriceFoldPredictor()
    predictor.fit(train_rows)
    offsets = sorted({row.feature_offset_seconds for row in validation_rows})
    candidates: list[OffsetCandidateReport] = []
    for offset_seconds in offsets:
        rows = rows_at_offset(validation_rows, offset_seconds)
        if not rows:
            continue
        horizon_seconds = rows[0].horizon_seconds
        if offset_seconds >= horizon_seconds:
            continue
        if any(row.horizon_seconds != horizon_seconds for row in rows):
            raise ValueError("validation rows at an offset must share one horizon")
        market_count = len(rows)
        if market_count < min_validation_markets:
            continue
        coverage = predictor.observed_price_coverage(rows)
        if coverage < min_market_price_coverage:
            continue
        probabilities = predictor.predict(rows)
        observed_count = sum(
            row.predictors.get("pm_up_price") is not None for row in rows
        )
        metrics = evaluate_probabilities(
            rows,
            probabilities,
            equal_market_weights(rows),
        )
        candidates.append(
            OffsetCandidateReport(
                offset_seconds=offset_seconds,
                market_count=market_count,
                observed_market_price_count=observed_count,
                fallback_count=market_count - observed_count,
                observed_market_price_coverage=coverage,
                metrics=metrics,
            )
        )

    if not candidates:
        raise ValueError("no validation offset satisfies coverage and market-count gates")
    ordered = tuple(sorted(candidates, key=lambda candidate: candidate.offset_seconds))
    selected = min(
        ordered,
        key=lambda candidate: (
            candidate.metrics.log_loss,
            candidate.metrics.brier_score,
            candidate.offset_seconds,
        ),
    )
    return OffsetSelection(
        selected_offset_seconds=selected.offset_seconds,
        candidates=ordered,
    )
