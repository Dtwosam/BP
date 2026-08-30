from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from bp_engine.storage import schema


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _json_safe(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _bucket_bounds(probability: float) -> tuple[int, int]:
    bounded = min(max(probability, 0.0), 1.0)
    lower = min(int((bounded * 100) // 5) * 5, 95)
    return lower, lower + 5


def summarize_performance(
    predictions: Iterable[Mapping[str, Any]],
    evaluations: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    prediction_rows = [dict(row) for row in predictions]
    evaluation_by_id = {
        str(row["prediction_id"]): dict(row)
        for row in evaluations
        if row.get("prediction_id") is not None
    }
    by_horizon: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in prediction_rows:
        by_horizon[int(row["horizon_seconds"])].append(row)

    result: list[dict[str, Any]] = []
    for horizon in sorted(by_horizon):
        rows = by_horizon[horizon]
        evaluated: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for prediction in rows:
            evaluation = evaluation_by_id.get(str(prediction["prediction_id"]))
            if evaluation is not None and evaluation.get("official_target") is not None:
                evaluated.append((prediction, evaluation))

        total = len(rows)
        evaluated_count = len(evaluated)
        accuracy = _mean([1.0 if bool(ev.get("correct")) else 0.0 for _, ev in evaluated])
        brier = _mean(
            [
                float(ev["calibrated_brier"])
                for _, ev in evaluated
                if ev.get("calibrated_brier") is not None
            ]
        )
        log_loss = _mean(
            [
                float(ev["calibrated_log_loss"])
                for _, ev in evaluated
                if ev.get("calibrated_log_loss") is not None
            ]
        )

        buckets: dict[tuple[int, int], list[tuple[float, int]]] = defaultdict(list)
        for prediction, evaluation in evaluated:
            probability = _as_float(prediction.get("calibrated_probability"))
            if probability is None:
                continue
            target = int(evaluation["official_target"])
            buckets[_bucket_bounds(probability)].append((probability, target))

        calibration_buckets = []
        for bounds in sorted(buckets):
            values = buckets[bounds]
            lower, upper = bounds
            calibration_buckets.append(
                {
                    "label": f"{lower}-{upper}%",
                    "count": len(values),
                    "mean_probability": _mean([probability for probability, _ in values]),
                    "observed_up_rate": _mean([float(target) for _, target in values]),
                }
            )

        result.append(
            {
                "horizon_seconds": horizon,
                "total_predictions": total,
                "evaluated_predictions": evaluated_count,
                "coverage": evaluated_count / total if total else None,
                "accuracy": accuracy,
                "calibrated_brier": brier,
                "calibrated_log_loss": log_loss,
                "calibration_buckets": calibration_buckets,
            }
        )
    return result


def _paper_evidence(repository: Any, *, history_limit: int) -> tuple[bool, dict[str, Any]]:
    reader = getattr(repository, "get_paper_execution_evidence", None)
    if not callable(reader):
        return False, {
            "paper_pnl": {
                "status": "UNAVAILABLE_UNTIL_PHASE_12",
                "value": None,
            },
            "paper_orders": [],
            "paper_fills": [],
            "paper_settlements": [],
        }
    evidence = dict(reader(history_limit=history_limit))
    return True, {
        "paper_pnl": evidence.get("paper_pnl"),
        "paper_orders": list(evidence.get("paper_orders", [])),
        "paper_fills": list(evidence.get("paper_fills", [])),
        "paper_settlements": list(evidence.get("paper_settlements", [])),
    }


def _default_live_readiness() -> dict[str, Any]:
    return {
        "eligible": False,
        "authorized": False,
        "kill_switch_engaged": True,
        "geoblock_blocked": None,
        "country": None,
        "region": None,
        "wallet_configured": False,
        "reconciliation_status": "unavailable",
        "critical_discrepancy_count": None,
    }


def _normalize_live_readiness(evidence: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "eligible": evidence.get("eligible") is True,
        "authorized": evidence.get("activation_authorized") is True,
        "kill_switch_engaged": evidence.get("kill_switch_engaged") is not False,
        "geoblock_blocked": evidence.get("geoblock_blocked"),
        "country": evidence.get("country"),
        "region": evidence.get("region"),
        "wallet_configured": evidence.get("wallet_configured") is True,
        "reconciliation_status": str(
            evidence.get("reconciliation_status") or "unavailable"
        ),
        "critical_discrepancy_count": evidence.get("critical_discrepancy_count"),
    }


def _database_live_readiness(repository: Any) -> dict[str, Any] | None:
    engine = getattr(repository, "engine", None)
    if engine is None:
        return None
    try:
        with engine.connect() as connection:
            readiness = connection.execute(
                select(schema.live_readiness_checks)
                .order_by(
                    schema.live_readiness_checks.c.observed_at.desc(),
                    schema.live_readiness_checks.c.id.desc(),
                )
                .limit(1)
            ).mappings().first()
            reconciliation = connection.execute(
                select(schema.live_reconciliation_runs)
                .order_by(
                    schema.live_reconciliation_runs.c.observed_at.desc(),
                    schema.live_reconciliation_runs.c.id.desc(),
                )
                .limit(1)
            ).mappings().first()
    except Exception:
        return None

    if readiness is None:
        return None
    source = dict(readiness.get("evidence") or {})
    activation = dict(source.get("activation") or {})
    kill_switch = dict(source.get("kill_switch") or {})
    geoblock = dict(source.get("geoblock") or {})
    reconciliation_evidence = dict(source.get("reconciliation") or {})
    return {
        "eligible": readiness.get("eligible") is True,
        "activation_authorized": activation.get("authorized") is True,
        "kill_switch_engaged": kill_switch.get("engaged") is not False,
        "geoblock_blocked": geoblock.get("blocked"),
        "country": geoblock.get("country"),
        "region": geoblock.get("region"),
        "wallet_configured": source.get("wallet_configured") is True,
        "reconciliation_status": reconciliation_evidence.get("status") or "unavailable",
        "critical_discrepancy_count": (
            None if reconciliation is None else reconciliation.get("critical_count")
        ),
    }


def _live_readiness_evidence(repository: Any) -> dict[str, Any]:
    reader = getattr(repository, "get_live_readiness_evidence", None)
    if callable(reader):
        try:
            return _normalize_live_readiness(dict(reader()))
        except Exception:
            return _default_live_readiness()
    evidence = _database_live_readiness(repository)
    if evidence is None:
        return _default_live_readiness()
    return _normalize_live_readiness(evidence)


def build_dashboard_snapshot(
    repository: Any,
    *,
    now: datetime,
    history_limit: int = 100,
) -> dict[str, Any]:
    active_markets = list(repository.list_active_markets(now))
    feed_health = list(repository.list_feed_health(now))
    history = list(repository.list_predictions(limit=history_limit))
    performance_predictions = list(repository.list_performance_predictions())
    performance_evaluations = list(repository.list_evaluations())
    paper_available, paper = _paper_evidence(repository, history_limit=history_limit)
    live_readiness = _live_readiness_evidence(repository)

    return _json_safe(
        {
            "generated_at": now,
            "execution_available": False,
            "mode": {
                "trading_mode": "RESEARCH",
                "live_trading_enabled": False,
                "execution_available": False,
                "paper_execution_available": paper_available,
            },
            "live_readiness": live_readiness,
            "active_markets": active_markets,
            "feed_health": feed_health,
            "performance": summarize_performance(
                performance_predictions,
                performance_evaluations,
            ),
            "prediction_history": history,
            **paper,
        }
    )