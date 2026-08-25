from __future__ import annotations

import math
from dataclasses import asdict
from datetime import UTC
from statistics import median
from typing import Any

from bp_engine.backtesting.execution import selected_observed_ask
from bp_engine.modeling.metrics import evaluate_probabilities
from bp_engine.modeling.models import SupervisedRow
from bp_engine.modeling.split import equal_market_weights

_UTC_SESSIONS = ("00-06", "06-12", "12-18", "18-24")
_VOLATILITY_REGIMES = ("low", "high", "unknown")
_EXECUTION_REGIMES = ("executable", "unavailable")


def utc_session_regime(row: SupervisedRow) -> str:
    value = row.market_start_at
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("market_start_at must be timezone-aware")
    hour = value.astimezone(UTC).hour
    if hour < 6:
        return "00-06"
    if hour < 12:
        return "06-12"
    if hour < 18:
        return "12-18"
    return "18-24"


def training_volatility_threshold(
    train_rows: tuple[SupervisedRow, ...], offset_seconds: int
) -> float | None:
    if offset_seconds <= 0:
        raise ValueError("offset_seconds must be positive")
    values: list[float] = []
    for row in train_rows:
        if row.feature_offset_seconds != offset_seconds:
            continue
        value = row.predictors.get("coinbase_realized_vol_15m")
        if value is None:
            continue
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError("coinbase_realized_vol_15m must be finite when present")
        values.append(numeric)
    return float(median(values)) if values else None


def volatility_regime(row: SupervisedRow, threshold: float | None) -> str:
    if threshold is None:
        return "unknown"
    if not math.isfinite(threshold):
        raise ValueError("volatility threshold must be finite")
    value = row.predictors.get("coinbase_realized_vol_15m")
    if value is None:
        return "unknown"
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError("coinbase_realized_vol_15m must be finite when present")
    return "low" if numeric <= threshold else "high"


def _group_report(
    rows: tuple[SupervisedRow, ...], probabilities: tuple[float, ...]
) -> dict[str, Any]:
    market_count = len({row.condition_id for row in rows})
    if not rows:
        return {"market_count": 0, "metrics": None}
    metrics = evaluate_probabilities(rows, probabilities, equal_market_weights(rows))
    return {"market_count": market_count, "metrics": asdict(metrics)}


def _reports_for_labels(
    rows: tuple[SupervisedRow, ...],
    probabilities: tuple[float, ...],
    labels: tuple[str, ...],
    row_labels: tuple[str, ...],
) -> dict[str, Any]:
    reports: dict[str, Any] = {}
    for label in labels:
        selected = tuple(
            (row, probability)
            for row, probability, row_label in zip(
                rows, probabilities, row_labels, strict=True
            )
            if row_label == label
        )
        group_rows = tuple(item[0] for item in selected)
        group_probabilities = tuple(item[1] for item in selected)
        reports[label] = _group_report(group_rows, group_probabilities)
    return reports


def regime_metrics(
    rows: tuple[SupervisedRow, ...],
    probabilities: tuple[float, ...],
    *,
    volatility_threshold: float | None,
) -> dict[str, Any]:
    if not rows:
        raise ValueError("rows must not be empty")
    if len(rows) != len(probabilities):
        raise ValueError("rows and probabilities must have equal length")

    utc_labels = tuple(utc_session_regime(row) for row in rows)
    volatility_labels = tuple(
        volatility_regime(row, volatility_threshold) for row in rows
    )
    execution_labels = tuple(
        (
            "executable"
            if selected_observed_ask(row, probability) is not None
            else "unavailable"
        )
        for row, probability in zip(rows, probabilities, strict=True)
    )
    return {
        "utc_session": _reports_for_labels(
            rows, probabilities, _UTC_SESSIONS, utc_labels
        ),
        "volatility": _reports_for_labels(
            rows, probabilities, _VOLATILITY_REGIMES, volatility_labels
        ),
        "execution_availability": _reports_for_labels(
            rows, probabilities, _EXECUTION_REGIMES, execution_labels
        ),
    }
