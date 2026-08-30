from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from math import isfinite

import numpy as np


def _as_decimal(value: object, *, field: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not result.is_finite():
        raise ValueError(f"{field} must be finite")
    return result


def _metric_mean(rows: Sequence[Mapping[str, object]], field: str) -> float | None:
    if not rows:
        return None
    values = [_as_decimal(row[field], field=field) for row in rows]
    return float(sum(values, Decimal("0")) / Decimal(len(values)))


def _bootstrap_mean_interval(
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


def _gate(status: str, reason: str) -> dict[str, str]:
    return {"status": status, "reason": reason}


def build_prospective_evidence_report(
    *,
    settlements: Sequence[Mapping[str, object]],
    evaluations: Sequence[Mapping[str, object]],
    reconciliation: Mapping[str, object],
    master_live_gate: Mapping[str, object],
    bootstrap_seed: int = 14,
    bootstrap_resamples: int = 10_000,
) -> dict[str, object]:
    """Build a read-only evidence report without changing the live gate."""
    realized = [
        _as_decimal(row["realized_pnl"], field="realized_pnl") for row in settlements
    ]
    total = sum(realized, Decimal("0"))
    mean = total / Decimal(len(realized)) if realized else None
    interval = _bootstrap_mean_interval(
        realized,
        seed=bootstrap_seed,
        resamples=bootstrap_resamples,
    )

    if not realized:
        profitability_gate = _gate(
            "insufficient_evidence",
            "No settled prospective paper trades are available.",
        )
    elif total <= 0:
        profitability_gate = _gate(
            "fail",
            "Realized after-cost prospective paper P&L is not positive.",
        )
    elif interval is not None and float(interval["lower"]) > 0:
        profitability_gate = _gate(
            "pass",
            "Realized after-cost P&L is positive and its bootstrap mean interval is above zero.",
        )
    else:
        profitability_gate = _gate(
            "insufficient_evidence",
            "Realized P&L is positive but uncertainty still includes non-positive expectancy.",
        )

    reconciliation_status = str(reconciliation.get("status", "")).upper()
    raw_violation_count = reconciliation.get("violation_count")
    violation_count = int(raw_violation_count) if raw_violation_count is not None else None
    if reconciliation_status == "OK" and violation_count in (None, 0):
        reconciliation_gate = _gate(
            "pass",
            "Paper order, fill, settlement, and reconciliation evidence is internally consistent.",
        )
    elif reconciliation_status == "VIOLATION" or (
        violation_count is not None and violation_count > 0
    ):
        reconciliation_gate = _gate(
            "fail",
            "Paper execution reconciliation contains one or more violations.",
        )
    else:
        reconciliation_gate = _gate(
            "insufficient_evidence",
            "Paper execution reconciliation status is unavailable or unrecognized.",
        )

    calibration = {
        "evaluation_count": len(evaluations),
        "raw_brier_mean": _metric_mean(evaluations, "raw_brier"),
        "raw_log_loss_mean": _metric_mean(evaluations, "raw_log_loss"),
        "calibrated_brier_mean": _metric_mean(evaluations, "calibrated_brier"),
        "calibrated_log_loss_mean": _metric_mean(evaluations, "calibrated_log_loss"),
    }
    for value in calibration.values():
        if isinstance(value, float) and not isfinite(value):
            raise ValueError("calibration metrics must be finite")

    return {
        "schema_version": 1,
        "automatic_promotion": False,
        "sample": {
            "settled_trade_count": len(realized),
            "evaluation_count": len(evaluations),
        },
        "after_cost_pnl": {
            "realized_total_usd": str(total),
            "realized_mean_usd": str(mean) if mean is not None else None,
            "mean_95pct_ci_usd": interval,
        },
        "calibration": calibration,
        "reconciliation": dict(reconciliation),
        "evidence_gates": {
            "sufficiently_large_live_paper_sample_with_uncertainty": _gate(
                "insufficient_evidence",
                "No approved fixed sample-size threshold exists; report size and uncertainty only.",
            ),
            "positive_after_cost_profitability": profitability_gate,
            "calibration_acceptable": _gate(
                "insufficient_evidence",
                "No approved numerical prospective calibration acceptance threshold exists.",
            ),
            "order_execution_and_reconciliation_tested": reconciliation_gate,
        },
        "master_live_gate": dict(master_live_gate),
    }
