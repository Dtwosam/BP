from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
import math

import numpy as np


@dataclass(frozen=True)
class BootstrapInterval:
    mean_delta: float
    lower: float
    upper: float
    resamples: int
    paired_markets: int


def _finite_values(values: Iterable[float]) -> tuple[float, ...]:
    materialized = tuple(float(value) for value in values)
    if any(not math.isfinite(value) for value in materialized):
        raise ValueError("values must be finite")
    return materialized


def paired_bootstrap_mean_delta(
    pairs: Sequence[tuple[str, float, float]],
    *,
    seed: int,
    resamples: int = 10_000,
) -> BootstrapInterval:
    """Return a deterministic paired bootstrap interval for challenger minus champion P&L."""

    if resamples <= 0:
        raise ValueError("resamples must be positive")
    if not pairs:
        raise ValueError("at least one paired market is required")

    ordered = sorted(pairs, key=lambda pair: pair[0])
    condition_ids = [condition_id for condition_id, _, _ in ordered]
    if any(not condition_id for condition_id in condition_ids):
        raise ValueError("condition id must be non-empty")
    if len(set(condition_ids)) != len(condition_ids):
        raise ValueError("condition ids must be unique")

    deltas: list[float] = []
    for _, champion_value, challenger_value in ordered:
        champion = float(champion_value)
        challenger = float(challenger_value)
        if not math.isfinite(champion) or not math.isfinite(challenger):
            raise ValueError("paired market values must be finite")
        deltas.append(challenger - champion)

    delta_array = np.asarray(deltas, dtype=float)
    paired_markets = len(deltas)
    generator = np.random.Generator(np.random.PCG64(seed))
    bootstrap_means = np.empty(resamples, dtype=float)
    for index in range(resamples):
        sampled_indices = generator.integers(0, paired_markets, size=paired_markets)
        bootstrap_means[index] = float(delta_array[sampled_indices].mean())

    lower, upper = np.percentile(bootstrap_means, (2.5, 97.5))
    return BootstrapInterval(
        mean_delta=float(delta_array.mean()),
        lower=float(lower),
        upper=float(upper),
        resamples=resamples,
        paired_markets=paired_markets,
    )


def max_drawdown(values: Iterable[float]) -> float:
    """Return maximum peak-to-trough drawdown on the cumulative realized-P&L path."""

    pnl_values = _finite_values(values)
    equity = 0.0
    peak = 0.0
    worst_drawdown = 0.0
    for pnl in pnl_values:
        equity += pnl
        peak = max(peak, equity)
        worst_drawdown = max(worst_drawdown, peak - equity)
    return worst_drawdown


def max_losing_streak(values: Iterable[float]) -> int:
    """Return the longest run of strictly negative realized-P&L outcomes."""

    pnl_values = _finite_values(values)
    longest = 0
    current = 0
    for pnl in pnl_values:
        if pnl < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest
