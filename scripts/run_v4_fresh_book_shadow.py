from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select, text

from bp_engine.config import Settings
from bp_engine.execution.fast_live_book import StreamingBookCache
from bp_engine.features.hashing import canonical_hash
from bp_engine.features.v4_models import V4FeatureTarget
from bp_engine.storage import schema
from bp_engine.v4_paper.fresh_book_shadow import (
    TARGET_NOTIONAL_USD,
    evaluate_v4_fresh_book_shadow,
)
from bp_engine.v4_paper.inference import (
    FROZEN_V4_MIN_EDGE,
    FROZEN_V4_MODEL_SHA256,
    FROZEN_V4_OFFSET_SECONDS,
    load_frozen_v4_bundle,
    predict_frozen_v4_probability,
)
from bp_engine.v4_paper.source_time_features import (
    MAX_FUTURE_SKEW_SECONDS,
    MAX_SOURCE_AGE_SECONDS,
    V4_SOURCE_TIME_FEATURE_VERSION,
    build_source_time_v4_features,
)

FORBIDDEN_ENV = (
    "POLYMARKET_PRIVATE_KEY",
    "POLYMARKET_WALLET_ADDRESS",
    "BP_TELEGRAM_BOT_TOKEN",
    "GOOGLE_APPLICATION_CREDENTIALS",
)
MAX_DECISION_LAG_SECONDS = 2.0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run frozen V4 at +240s with provider-source-time BTC features and a "
            "dedicated fresh Polymarket ask stream. Emits paper-only JSONL."
        )
    )
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--env-file", default="/etc/bp/bp.env")
    parser.add_argument("--run-seconds", type=float, default=3600.0)
    parser.add_argument("--poll-seconds", type=float, default=0.10)
    parser.add_argument("--quote-wait-seconds", type=float, default=1.0)
    parser.add_argument("--quote-fresh-seconds", type=float, default=0.25)
    parser.add_argument("--market-lookahead-seconds", type=float, default=600.0)
    parser.add_argument(
        "--max-decision-lag-seconds",
        type=float,
        default=MAX_DECISION_LAG_SECONDS,
    )
    return parser.parse_args()


def _require_safe_environment(env_file: str) -> None:
    for name in FORBIDDEN_ENV:
        if os.environ.get(name):
            raise SystemExit(f"forbidden environment present: {name}")

    path = Path(env_file)
    if not path.is_file():
        raise SystemExit(f"environment file missing: {env_file}")
    names = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        names.add(line.split("=", 1)[0].strip())
    forbidden = sorted(names.intersection(FORBIDDEN_ENV))
    if forbidden:
        raise SystemExit(
            "forbidden trading/credential names present in environment file: "
            + ",".join(forbidden)
        )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")), flush=True)


def _subscribe_nearby_markets(
    connection,
    cache: StreamingBookCache,
    *,
    now: datetime,
    lookahead_seconds: float,
) -> int:
    future = now + timedelta(seconds=lookahead_seconds)
    rows = connection.execute(
        select(
            schema.polymarket_markets.c.up_token_id,
            schema.polymarket_markets.c.down_token_id,
        ).where(
            schema.polymarket_markets.c.horizon_seconds == 300,
            schema.polymarket_markets.c.active.is_(True),
            schema.polymarket_markets.c.end_at >= now,
            schema.polymarket_markets.c.start_at
            <= future - timedelta(seconds=FROZEN_V4_OFFSET_SECONDS),
        )
    ).all()
    tokens = sorted(
        {
            str(token)
            for row in rows
            for token in row
            if str(token or "").strip()
        }
    )
    cache.subscribe(tokens)
    return len(tokens)


def _due_markets(
    connection,
    *,
    started_at: datetime,
    now: datetime,
) -> list[dict[str, Any]]:
    earliest_start = started_at - timedelta(seconds=FROZEN_V4_OFFSET_SECONDS)
    latest_start = now - timedelta(seconds=FROZEN_V4_OFFSET_SECONDS)
    return [
        dict(row)
        for row in connection.execute(
            select(
                schema.polymarket_markets.c.condition_id,
                schema.polymarket_markets.c.slug,
                schema.polymarket_markets.c.horizon_seconds,
                schema.polymarket_markets.c.start_at,
                schema.polymarket_markets.c.end_at,
                schema.polymarket_markets.c.up_token_id,
                schema.polymarket_markets.c.down_token_id,
            )
            .where(
                schema.polymarket_markets.c.horizon_seconds == 300,
                schema.polymarket_markets.c.start_at >= earliest_start,
                schema.polymarket_markets.c.start_at <= latest_start,
                schema.polymarket_markets.c.end_at > now,
            )
            .order_by(
                schema.polymarket_markets.c.start_at,
                schema.polymarket_markets.c.condition_id,
            )
        ).mappings()
    ]


def _target(row: dict[str, Any]) -> V4FeatureTarget:
    return V4FeatureTarget(
        condition_id=str(row["condition_id"]),
        slug=str(row["slug"]),
        horizon_seconds=int(row["horizon_seconds"]),
        market_start_at=_utc(row["start_at"]),
        market_end_at=_utc(row["end_at"]),
    )


def _fresh_levels(
    cache: StreamingBookCache,
    token_id: str,
    *,
    wait_seconds: float,
) -> tuple[tuple[str, str], ...] | None:
    deadline = time.monotonic() + wait_seconds
    while True:
        levels = cache.snapshot(token_id)
        if levels is not None:
            return levels
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.01)


def _prediction_id(
    *,
    condition_id: str,
    decision_at: datetime,
    evidence_sha256: str,
) -> str:
    return canonical_hash(
        {
            "model_sha256": FROZEN_V4_MODEL_SHA256,
            "condition_id": condition_id,
            "decision_at": decision_at.isoformat(),
            "source_feature_version": V4_SOURCE_TIME_FEATURE_VERSION,
            "source_evidence_sha256": evidence_sha256,
        }
    )


def main() -> int:
    args = _parse_args()
    if args.run_seconds <= 0:
        raise SystemExit("--run-seconds must be greater than zero")
    if args.poll_seconds <= 0:
        raise SystemExit("--poll-seconds must be greater than zero")
    if args.quote_wait_seconds < 0:
        raise SystemExit("--quote-wait-seconds must be non-negative")
    if args.quote_fresh_seconds <= 0:
        raise SystemExit("--quote-fresh-seconds must be greater than zero")
    if args.market_lookahead_seconds <= 0:
        raise SystemExit("--market-lookahead-seconds must be greater than zero")
    if args.max_decision_lag_seconds <= 0:
        raise SystemExit("--max-decision-lag-seconds must be greater than zero")

    _require_safe_environment(args.env_file)
    settings = Settings(_env_file=args.env_file)
    if settings.live_trading_enabled:
        raise SystemExit("LIVE_TRADING_ENABLED must be false")
    if settings.max_trade_size_usd != 0:
        raise SystemExit("MAX_TRADE_SIZE_USD must be 0")
    if settings.max_daily_loss_usd != 0:
        raise SystemExit("MAX_DAILY_LOSS_USD must be 0")

    bundle = load_frozen_v4_bundle(args.model_path)
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={"options": "-c default_transaction_read_only=on"},
    )
    cache = StreamingBookCache(quote_fresh_seconds=args.quote_fresh_seconds)
    cache.start()
    started_at = datetime.now(UTC)
    deadline = time.monotonic() + args.run_seconds
    seen: set[str] = set()
    prediction_count = 0
    evaluated_count = 0
    quote_unavailable_count = 0
    decision_missed_count = 0
    subscribed = 0

    _emit(
        {
            "event": "v4_fresh_book_shadow_started",
            "started_at": started_at.isoformat(),
            "model_sha256": FROZEN_V4_MODEL_SHA256,
            "source_feature_version": V4_SOURCE_TIME_FEATURE_VERSION,
            "decision_offset_seconds": FROZEN_V4_OFFSET_SECONDS,
            "max_btc_source_age_seconds": MAX_SOURCE_AGE_SECONDS,
            "max_btc_future_skew_seconds": MAX_FUTURE_SKEW_SECONDS,
            "max_decision_lag_seconds": args.max_decision_lag_seconds,
            "quote_fresh_seconds": args.quote_fresh_seconds,
            "target_notional_usd": str(TARGET_NOTIONAL_USD),
            "frozen_min_edge": str(FROZEN_V4_MIN_EDGE),
            "database_read_only": True,
            "order_submission_enabled": False,
            "wallet_material_loaded": False,
            "holdout_labels_read": False,
            "model_refit_performed": False,
            "threshold_tuning_performed": False,
        }
    )

    try:
        while time.monotonic() < deadline:
            now = datetime.now(UTC)
            with engine.connect() as connection:
                read_only = connection.execute(
                    text("SHOW default_transaction_read_only")
                ).scalar_one()
                if read_only != "on":
                    raise SystemExit("database connection is not read-only")

                subscribed = max(
                    subscribed,
                    _subscribe_nearby_markets(
                        connection,
                        cache,
                        now=now,
                        lookahead_seconds=args.market_lookahead_seconds,
                    ),
                )
                rows = _due_markets(
                    connection,
                    started_at=started_at,
                    now=now,
                )

                for row in rows:
                    condition_id = str(row["condition_id"])
                    if condition_id in seen:
                        continue
                    target = _target(row)
                    decision_at = _utc(target.market_start_at) + timedelta(
                        seconds=FROZEN_V4_OFFSET_SECONDS
                    )
                    if decision_at < started_at:
                        continue
                    initial_lag = (now - decision_at).total_seconds()
                    if initial_lag > args.max_decision_lag_seconds:
                        seen.add(condition_id)
                        decision_missed_count += 1
                        _emit(
                            {
                                "event": "v4_fresh_book_shadow_decision_missed",
                                "condition_id": condition_id,
                                "decision_at": decision_at.isoformat(),
                                "observed_at": now.isoformat(),
                                "decision_lag_seconds": initial_lag,
                                "trade": False,
                                "reason": "decision_lag_exceeded",
                                "order_submission_enabled": False,
                            }
                        )
                        continue

                    features = build_source_time_v4_features(
                        connection,
                        target,
                        decision_at=decision_at,
                    )
                    probability_up = predict_frozen_v4_probability(
                        bundle,
                        features.predictors,
                    )
                    recorded_at = datetime.now(UTC)
                    decision_lag = (recorded_at - decision_at).total_seconds()
                    if decision_lag > args.max_decision_lag_seconds:
                        seen.add(condition_id)
                        decision_missed_count += 1
                        _emit(
                            {
                                "event": "v4_fresh_book_shadow_decision_missed",
                                "condition_id": condition_id,
                                "decision_at": decision_at.isoformat(),
                                "observed_at": recorded_at.isoformat(),
                                "decision_lag_seconds": decision_lag,
                                "trade": False,
                                "reason": "feature_inference_lag_exceeded",
                                "order_submission_enabled": False,
                            }
                        )
                        continue

                    evidence_mapping = features.evidence_mapping()
                    evidence_sha256 = canonical_hash(evidence_mapping)
                    prediction_id = _prediction_id(
                        condition_id=condition_id,
                        decision_at=decision_at,
                        evidence_sha256=evidence_sha256,
                    )
                    prediction = {
                        "prediction_id": prediction_id,
                        "model_sha256": FROZEN_V4_MODEL_SHA256,
                        "source_feature_version": V4_SOURCE_TIME_FEATURE_VERSION,
                        "condition_id": condition_id,
                        "decision_at": decision_at,
                        "recorded_at": recorded_at,
                        "probability_up": probability_up,
                        "up_token_id": str(row["up_token_id"]),
                        "down_token_id": str(row["down_token_id"]),
                    }
                    seen.add(condition_id)
                    prediction_count += 1

                    selected_side = "up" if probability_up >= 0.5 else "down"
                    token_id = str(
                        row[
                            "up_token_id"
                            if selected_side == "up"
                            else "down_token_id"
                        ]
                    )
                    cache.subscribe([token_id])

                    _emit(
                        {
                            "event": "v4_source_time_prediction",
                            "prediction_id": prediction_id,
                            "condition_id": condition_id,
                            "decision_at": decision_at.isoformat(),
                            "recorded_at": recorded_at.isoformat(),
                            "decision_lag_seconds": decision_lag,
                            "probability_up": probability_up,
                            "selected_side": selected_side,
                            "selected_token_id": token_id,
                            "model_sha256": FROZEN_V4_MODEL_SHA256,
                            "source_feature_version": V4_SOURCE_TIME_FEATURE_VERSION,
                            "source_evidence_sha256": evidence_sha256,
                            "source_evidence": evidence_mapping,
                            "missing_flags": features.missing_flags,
                            "predictors": features.predictors,
                            "holdout_labels_read": False,
                            "order_submission_enabled": False,
                        }
                    )

                    levels = _fresh_levels(
                        cache,
                        token_id,
                        wait_seconds=args.quote_wait_seconds,
                    )
                    quote_at = datetime.now(UTC)
                    if levels is None:
                        quote_unavailable_count += 1
                        _emit(
                            {
                                "event": "v4_fresh_book_shadow_quote_unavailable",
                                "prediction_id": prediction_id,
                                "condition_id": condition_id,
                                "prediction_recorded_at": recorded_at.isoformat(),
                                "quote_checked_at": quote_at.isoformat(),
                                "token_id": token_id,
                                "trade": False,
                                "reason": "fresh_quote_unavailable",
                                "order_submission_enabled": False,
                            }
                        )
                        continue

                    result = evaluate_v4_fresh_book_shadow(
                        prediction,
                        levels,
                        quote_observed_at=quote_at,
                    )
                    evaluated_count += 1
                    payload = result.as_mapping()
                    payload.update(
                        {
                            "event": "v4_fresh_book_shadow_evaluated",
                            "source_evidence_sha256": evidence_sha256,
                            "decision_lag_seconds": decision_lag,
                            "order_submission_enabled": False,
                            "wallet_material_loaded": False,
                            "holdout_labels_read": False,
                        }
                    )
                    _emit(payload)

            time.sleep(args.poll_seconds)
    finally:
        cache.stop()
        engine.dispose()

    _emit(
        {
            "event": "v4_fresh_book_shadow_completed",
            "completed_at": datetime.now(UTC).isoformat(),
            "seen_market_count": len(seen),
            "prediction_count": prediction_count,
            "evaluated_count": evaluated_count,
            "quote_unavailable_count": quote_unavailable_count,
            "decision_missed_count": decision_missed_count,
            "subscribed_token_count_high_water": subscribed,
            "database_read_only": True,
            "database_writes_performed": False,
            "order_submission_enabled": False,
            "order_submission_performed": False,
            "wallet_material_loaded": False,
            "holdout_labels_read": False,
            "model_refit_performed": False,
            "threshold_tuning_performed": False,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
