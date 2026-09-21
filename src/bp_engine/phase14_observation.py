from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine

from bp_engine.config import Settings, TradingMode
from bp_engine.features.v4_coverage import build_v4_coverage_report
from bp_engine.features.v4_forward import V4_FORWARD_EPOCH
from bp_engine.storage.maintenance import build_composite_storage_health
from bp_engine.v3_paper.report import build_v3_paper_report


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(UTC)


def _zero_money_safety(settings: Settings) -> dict[str, Any]:
    mode = settings.mode.value if isinstance(settings.mode, TradingMode) else str(settings.mode)
    return {
        "mode": mode,
        "live_trading_enabled": bool(settings.live_trading_enabled),
        "max_trade_size_usd": float(settings.max_trade_size_usd),
        "max_daily_loss_usd": float(settings.max_daily_loss_usd),
        "research_zero_money": (
            mode == TradingMode.RESEARCH.value
            and settings.live_trading_enabled is False
            and float(settings.max_trade_size_usd) == 0.0
            and float(settings.max_daily_loss_usd) == 0.0
        ),
    }


def _v4_integrity(coverage: dict[str, Any]) -> dict[str, Any]:
    return {
        "future_cutoff_violation_count": int(
            coverage.get("future_cutoff_violation_count", 0)
        ),
        "polymarket_predictor_key_count": int(
            coverage.get("polymarket_predictor_key_count", 0)
        ),
        "regime_invariant_violation_count": int(
            coverage.get("regime_invariant_violation_count", 0)
        ),
        "policy_selected": bool(coverage.get("policy_selected", False)),
        "training_run": bool(coverage.get("training_run", False)),
        "automatic_promotion": bool(coverage.get("automatic_promotion", False)),
    }


def build_phase14_observation_report(
    engine: Engine,
    settings: Settings,
    *,
    now: datetime | None = None,
    recent_limit: int = 20,
    storage_path: Path | str | None = None,
) -> dict[str, Any]:
    """Compose the authorized Phase 14 observation surfaces without mutation."""
    observed_at = datetime.now(UTC) if now is None else _utc(now)
    target = Path(
        storage_path
        or settings.storage_health_path
        or settings.storage_archive_dir
    )
    if not target.is_dir():
        raise FileNotFoundError(
            f"observation storage path must already exist: {target}"
        )

    v3 = build_v3_paper_report(engine, recent_limit=recent_limit)
    with engine.connect() as connection:
        v4 = build_v4_coverage_report(
            connection,
            epoch_start=V4_FORWARD_EPOCH,
        )
    storage = build_composite_storage_health(
        engine,
        target,
        settings,
        now=observed_at,
    )

    safety = _zero_money_safety(settings)
    v4_integrity = _v4_integrity(v4)
    v4_integrity_ok = (
        v4_integrity["future_cutoff_violation_count"] == 0
        and v4_integrity["polymarket_predictor_key_count"] == 0
        and v4_integrity["regime_invariant_violation_count"] == 0
        and v4_integrity["policy_selected"] is False
        and v4_integrity["training_run"] is False
        and v4_integrity["automatic_promotion"] is False
    )
    storage_guards = storage.get("guards") or {}
    storage_ok = (
        storage.get("status") == "ok"
        and storage.get("storage_mode") == "partitioned"
        and all(
            storage_guards.get(name) is True
            for name in (
                "maintenance_fresh",
                "current_partition_present",
                "retention_current",
            )
        )
    )

    return {
        "generated_at": observed_at,
        "mode": "phase14_observation_only",
        "safety": safety,
        "v3_paper": v3,
        "v4_forward_coverage": v4,
        "storage": storage,
        "integrity": {
            "research_zero_money": safety["research_zero_money"],
            "v4": {
                **v4_integrity,
                "ok": v4_integrity_ok,
            },
            "storage_ok": storage_ok,
            "all_observation_guards_ok": (
                safety["research_zero_money"]
                and v4_integrity_ok
                and storage_ok
            ),
        },
    }
