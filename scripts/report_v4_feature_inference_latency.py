from __future__ import annotations

import argparse
import json
import math
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from statistics import median
from typing import Any

from sqlalchemy import create_engine, event, select, text

from bp_engine.storage import schema
from bp_engine.config import Settings, TradingMode
from bp_engine.features.v4_models import V4FeatureTarget
from bp_engine.v4_paper.inference import (
    FROZEN_V4_MODEL_SHA256,
    FROZEN_V4_OFFSET_SECONDS,
    load_frozen_v4_bundle,
    predict_frozen_v4_probability,
)
from bp_engine.v4_paper.source_time_features import (
    build_source_time_v4_features,
    probe_core_source_time_v4_readiness,
)


@dataclass
class SqlTimingCollector:
    stage: str = "idle"

    def __post_init__(self) -> None:
        self.samples: dict[str, list[float]] = defaultdict(list)

    def set_stage(self, stage: str) -> None:
        self.stage = stage

    def before(self, _conn, _cursor, _statement, _parameters, context, _executemany) -> None:
        context._v4_latency_started = time.perf_counter()
        context._v4_latency_stage = self.stage

    def after(self, _conn, _cursor, _statement, _parameters, context, _executemany) -> None:
        started = getattr(context, "_v4_latency_started", None)
        stage = getattr(context, "_v4_latency_stage", self.stage)
        if started is None:
            return
        self.samples[str(stage)].append(time.perf_counter() - started)

    def summary(self, stage: str) -> dict[str, float | int | None]:
        values = sorted(self.samples.get(stage, ()))
        if not values:
            return {
                "statement_count": 0,
                "total_seconds": 0.0,
                "median_seconds": None,
                "p95_seconds": None,
                "max_seconds": None,
            }
        p95_index = min(len(values) - 1, max(0, math.ceil(len(values) * 0.95) - 1))
        return {
            "statement_count": len(values),
            "total_seconds": round(sum(values), 6),
            "median_seconds": round(float(median(values)), 6),
            "p95_seconds": round(values[p95_index], 6),
            "max_seconds": round(values[-1], 6),
        }


def _target_from_row(row: dict[str, Any]) -> V4FeatureTarget:
    return V4FeatureTarget(
        condition_id=str(row["condition_id"]),
        slug=str(row["slug"]),
        horizon_seconds=int(row["horizon_seconds"]),
        market_start_at=row["start_at"],
        market_end_at=row["end_at"],
    )


def _market_row(connection, condition_id: str) -> dict[str, Any]:
    row = connection.execute(
        select(
            schema.polymarket_markets.c.condition_id,
            schema.polymarket_markets.c.slug,
            schema.polymarket_markets.c.horizon_seconds,
            schema.polymarket_markets.c.start_at,
            schema.polymarket_markets.c.end_at,
        ).where(schema.polymarket_markets.c.condition_id == condition_id)
    ).mappings().one_or_none()
    if row is None:
        raise SystemExit(f"condition not found: {condition_id}")
    return dict(row)


def _timed(callable_):
    started = time.perf_counter()
    result = callable_()
    return result, time.perf_counter() - started


def build_report(
    *,
    settings: Settings,
    engine,
    model_path: Path,
    condition_ids: list[str],
) -> dict[str, Any]:
    if settings.mode is not TradingMode.RESEARCH:
        raise SystemExit("MODE must be research")
    if settings.live_trading_enabled:
        raise SystemExit("LIVE_TRADING_ENABLED must be false")
    if settings.max_trade_size_usd != 0:
        raise SystemExit("MAX_TRADE_SIZE_USD must be 0")
    if settings.max_daily_loss_usd != 0:
        raise SystemExit("MAX_DAILY_LOSS_USD must be 0")
    if settings.recorder_batch_size != 100:
        raise SystemExit("RECORDER_BATCH_SIZE must be 100")

    bundle, model_load_seconds = _timed(lambda: load_frozen_v4_bundle(model_path))

    collector = SqlTimingCollector()
    event.listen(engine, "before_cursor_execute", collector.before)
    event.listen(engine, "after_cursor_execute", collector.after)

    condition_reports: list[dict[str, Any]] = []
    try:
        with engine.connect() as connection:
            collector.set_stage("safety")
            if connection.execute(
                text("SHOW default_transaction_read_only")
            ).scalar_one() != "on":
                raise SystemExit("database connection is not read-only")

            for condition_id in condition_ids:
                target_stage = f"{condition_id}:target"
                readiness_stage = f"{condition_id}:readiness"
                feature_stage = f"{condition_id}:features"

                collector.set_stage(target_stage)
                target = _target_from_row(_market_row(connection, condition_id))
                decision_at = target.market_start_at + timedelta(
                    seconds=FROZEN_V4_OFFSET_SECONDS
                )

                collector.set_stage(readiness_stage)
                readiness, readiness_seconds = _timed(
                    lambda: probe_core_source_time_v4_readiness(
                        connection,
                        target,
                        decision_at=decision_at,
                    )
                )

                collector.set_stage(feature_stage)
                features, feature_seconds = _timed(
                    lambda: build_source_time_v4_features(
                        connection,
                        target,
                        decision_at=decision_at,
                    )
                )

                inference_seconds: float | None = None
                probability_up: float | None = None
                inference_error: str | None = None
                try:
                    probability_up, inference_seconds = _timed(
                        lambda: predict_frozen_v4_probability(
                            bundle,
                            features.predictors,
                            missing_flags=features.missing_flags,
                        )
                    )
                except Exception as exc:
                    inference_error = f"{type(exc).__name__}: {exc}"

                condition_reports.append(
                    {
                        "condition_id": condition_id,
                        "decision_at": decision_at.isoformat(),
                        "readiness_seconds": round(readiness_seconds, 6),
                        "feature_build_seconds": round(feature_seconds, 6),
                        "inference_seconds": (
                            None
                            if inference_seconds is None
                            else round(inference_seconds, 6)
                        ),
                        "pipeline_seconds": round(
                            readiness_seconds
                            + feature_seconds
                            + (inference_seconds or 0.0),
                            6,
                        ),
                        "readiness_core_source_ready": readiness.core_source_ready,
                        "readiness_ineligible_reasons": list(
                            readiness.core_source_ineligible_reasons()
                        ),
                        "feature_core_source_ready": features.core_source_ready,
                        "feature_ineligible_reasons": list(
                            features.core_source_ineligible_reasons()
                        ),
                        "predictor_count": len(features.predictors),
                        "missing_flag_count": len(features.missing_flags),
                        "probability_up": probability_up,
                        "inference_error": inference_error,
                        "sql": {
                            "target": collector.summary(target_stage),
                            "readiness": collector.summary(readiness_stage),
                            "features": collector.summary(feature_stage),
                        },
                    }
                )
    finally:
        collector.set_stage("idle")

    return {
        "report": "v4_feature_inference_latency_v1",
        "model_sha256": FROZEN_V4_MODEL_SHA256,
        "model_load_seconds": round(model_load_seconds, 6),
        "conditions": condition_reports,
        "safety": {
            "mode": "research",
            "live_trading_enabled": False,
            "max_trade_size_usd": 0,
            "max_daily_loss_usd": 0,
            "recorder_batch_size": 100,
            "database_read_only_required": True,
            "database_writes_performed": False,
            "service_mutation_performed": False,
            "order_submission_performed": False,
        },
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Replay selected V4 shadow conditions and time readiness, "
            "feature construction, SQL, and frozen inference."
        )
    )
    parser.add_argument("--env-file", default="/etc/bp/bp.env")
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--condition-id", action="append", required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    settings = Settings(_env_file=args.env_file)
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={
            "options": (
                "-c default_transaction_read_only=on "
                "-c statement_timeout=3000 "
                "-c application_name=bp-v4-feature-inference-latency"
            )
        },
    )
    try:
        report = build_report(
            settings=settings,
            engine=engine,
            model_path=Path(args.model_path),
            condition_ids=list(args.condition_id),
        )
    finally:
        engine.dispose()

    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    print("PHASE14_V4_FEATURE_INFERENCE_LATENCY_STATUS=PASS")
    print("DATABASE_ACCESS=read_only")
    print("DATABASE_WRITES_PERFORMED=false")
    print("SERVICE_MUTATION_PERFORMED=false")
    print("ORDER_SUBMISSION_PERFORMED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
