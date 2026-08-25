from __future__ import annotations

import math

from bp_engine.modeling.models import SupervisedRow


class PriorBaseline:
    def __init__(self) -> None:
        self.probability: float | None = None

    def fit(
        self, rows: tuple[SupervisedRow, ...], weights: tuple[float, ...]
    ) -> None:
        if len(rows) != len(weights) or not rows:
            raise ValueError("rows and weights must have equal non-zero length")
        total = sum(weights)
        if not math.isfinite(total) or total <= 0:
            raise ValueError("weights must have positive finite sum")
        self.probability = sum(
            row.target * weight for row, weight in zip(rows, weights, strict=True)
        ) / total

    def predict_proba(self, rows: tuple[SupervisedRow, ...]) -> tuple[float, ...]:
        if self.probability is None:
            raise RuntimeError("baseline must be fitted before prediction")
        return tuple(self.probability for _ in rows)


class MarketPriceBaseline:
    def __init__(self, fallback_probability: float) -> None:
        if not math.isfinite(fallback_probability) or not 0 <= fallback_probability <= 1:
            raise ValueError("fallback_probability must be in [0, 1]")
        self.fallback_probability = fallback_probability
        self.last_fallback_count = 0

    def predict_proba(self, rows: tuple[SupervisedRow, ...]) -> tuple[float, ...]:
        result: list[float] = []
        fallback_count = 0
        for row in rows:
            value = row.predictors.get("pm_up_price")
            if value is None:
                probability = self.fallback_probability
                fallback_count += 1
            else:
                probability = float(value)
                if not math.isfinite(probability):
                    raise ValueError("pm_up_price must be finite when present")
            result.append(min(max(probability, 1e-6), 1.0 - 1e-6))
        self.last_fallback_count = fallback_count
        return tuple(result)
