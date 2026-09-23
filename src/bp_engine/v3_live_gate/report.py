from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from math import isfinite, sqrt

import numpy as np

_ZERO = Decimal("0")


def _decimal(value: object, *, field: str) -> Decimal:
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not number.is_finite():
        raise ValueError(f"{field} must be finite")
    return number


def _bootstrap_mean(
    values: Sequence[Decimal],
    *,
    seed: int,
    resamples: int,
) -> dict[str, object] | None:
    if not values:
        return None
    if resamples <= 0:
        raise ValueError("bootstrap_resamples must be positive")
    sample = np.asarray([float(value) for value in values], dtype=float)
    if not np.isfinite(sample).all():
        raise ValueError("realized_pnl must be finite")
    generator = np.random.Generator(np.random.PCG64(seed))
    means = np.empty(resamples, dtype=float)
    for index in range(resamples):
        draw = generator.integers(0, len(sample), size=len(sample))
        means[index] = float(sample[draw].mean())
    lower, upper = np.percentile(means, [2.5, 97.5])
    return {
        "lower": float(lower),
        "upper": float(upper),
        "method": "deterministic_bootstrap_percentile",
        "resamples": resamples,
        "seed": seed,
    }


def _wilson(wins: int, total: int) -> dict[str, float] | None:
    if total <= 0:
        return None
    z = 1.959963984540054
    p = wins / total
    denom = 1.0 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    margin = z * sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denom
    return {
        "lower": center - margin,
        "upper": center + margin,
        "method": "wilson_95pct",
    }


def _max_drawdown(values: Sequence[Decimal]) -> Decimal:
    equity = peak = _ZERO
    worst = _ZERO
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def _max_losing_streak(values: Sequence[Decimal]) -> int:
    current = longest = 0
    for value in values:
        if value < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _mean(rows: Sequence[Mapping[str, object]], key: str) -> float | None:
    if not rows:
        return None
    values = [_decimal(row[key], field=key) for row in rows]
    value = float(sum(values, _ZERO) / Decimal(len(values)))
    if not isfinite(value):
        raise ValueError(f"{key} mean must be finite")
    return value


def build_v3_live_gate_report(
    *,
    settlements: Sequence[Mapping[str, object]],
    evaluations: Sequence[Mapping[str, object]],
    reconciliation: Mapping[str, object],
    user_authorized: bool,
    bootstrap_seed: int = 14,
    bootstrap_resamples: int = 10_000,
) -> dict[str, object]:
    """Build V3-only read-only evidence. Never enables live trading."""
    pnl = [_decimal(row["realized_pnl"], field="realized_pnl") for row in settlements]
    total = sum(pnl, _ZERO)
    wins = sum(value > 0 for value in pnl)
    losses = sum(value < 0 for value in pnl)
    gross_profit = sum((value for value in pnl if value > 0), _ZERO)
    gross_loss = -sum((value for value in pnl if value < 0), _ZERO)
    largest_winner = max((value for value in pnl if value > 0), default=_ZERO)
    without_largest = total - largest_winner
    interval = _bootstrap_mean(pnl, seed=bootstrap_seed, resamples=bootstrap_resamples)
    profit_factor = None if gross_loss == 0 else float(gross_profit / gross_loss)

    calibration = {
        "evaluation_count": len(evaluations),
        "raw_brier_mean": _mean(evaluations, "raw_brier"),
        "raw_log_loss_mean": _mean(evaluations, "raw_log_loss"),
        "calibrated_brier_mean": _mean(evaluations, "calibrated_brier"),
        "calibrated_log_loss_mean": _mean(evaluations, "calibrated_log_loss"),
    }
    recon_ok = str(reconciliation.get("status", "")).upper() == "OK" and int(
        reconciliation.get("violation_count", 0)
    ) == 0

    return {
        "schema_version": 1,
        "scope": "frozen_v3_live_gate_reassessment",
        "automatic_promotion": False,
        "live_trading_enabled": False,
        "real_money_mutation_performed": False,
        "explicit_user_live_authorization": "pass" if user_authorized else "fail",
        "sample": {
            "settled_trade_count": len(pnl),
            "wins": wins,
            "losses": losses,
            "win_rate": wins / len(pnl) if pnl else None,
            "win_rate_95pct_ci": _wilson(wins, len(pnl)),
        },
        "economics": {
            "realized_total_usd": str(total),
            "realized_mean_usd": str(total / Decimal(len(pnl))) if pnl else None,
            "mean_95pct_ci_usd": interval,
            "gross_profit_usd": str(gross_profit),
            "gross_loss_usd": str(gross_loss),
            "profit_factor": profit_factor,
            "max_drawdown_usd": str(_max_drawdown(pnl)),
            "max_losing_streak": _max_losing_streak(pnl),
            "largest_winner_usd": str(largest_winner),
            "pnl_excluding_largest_winner_usd": str(without_largest),
            "largest_winner_share_of_total": (
                float(largest_winner / total) if total > 0 else None
            ),
        },
        "calibration": calibration,
        "reconciliation": dict(reconciliation),
        "diagnostics": {
            "positive_total_pnl": total > 0,
            "bootstrap_mean_lower_bound_positive": bool(
                interval and float(interval["lower"]) > 0
            ),
            "profitable_without_largest_winner": without_largest > 0,
            "reconciliation_ok": recon_ok,
        },
        "gate_boundary": {
            "master_live_gate_mutated": False,
            "phase15_permitted": False,
            "reason": (
                "This report is evidence only. Geographic eligibility, risk/live "
                "readiness, calibration/sample sufficiency, and a complete Master "
                "live-gate decision remain separate gates."
            ),
        },
    }
