from __future__ import annotations

import argparse
import json
import math
import time
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from bp_engine.config import Settings, TradingMode
from bp_engine.v4_paper.source_time_features import (
    MAX_FUTURE_SKEW_SECONDS,
    MAX_SOURCE_AGE_SECONDS,
    _bybit_price,
)

DEFAULT_SAMPLES = 60
DEFAULT_INTERVAL_SECONDS = 0.5
LOOKBACK_SECONDS = 12
ROW_LIMIT = 8

# Sample all Bybit linear tickers, then exactly the two price-bearing
# event classes supported by V4. No mark/index/bid/ask substitution.
CATEGORIES = {
    "ticker_any": "event_type = 'ticker'",
    "ticker_last_price": (
        "event_type = 'ticker' AND payload->'data' ? 'lastPrice'"
    ),
    "trade_price": (
        "event_type = 'trade' "
        "AND jsonb_typeof(payload->'data') = 'array' "
        "AND jsonb_array_length(payload->'data') > 0"
    ),
}


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)


def _distribution(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * 0.95) - 1))
    return {
        "min": round(ordered[0], 6),
        "median": round(median(ordered), 6),
        "p95": round(ordered[index], 6),
        "max": round(ordered[-1], 6),
    }


def _load_rows(connection, *, category: str, observed_at: datetime):
    if category not in CATEGORIES:
        raise ValueError("unsupported event category")
    # A narrow, parent-partition-pruned read. No EXPLAIN ANALYZE or writes.
    sql = text(
        """
        SELECT id, event_type, source_timestamp, received_at, payload
        FROM raw_market_events
        WHERE source = 'bybit'
          AND stream = 'linear'
          AND instrument = 'BTCUSDT'
          AND source_timestamp IS NOT NULL
          AND received_at >= :lower
          AND received_at <= :observed_at
          AND source_timestamp <= :source_upper
          AND """
        + CATEGORIES[category]
        + """
        ORDER BY received_at DESC, id DESC
        LIMIT :limit
        """
    )
    return [
        dict(row)
        for row in connection.execute(
            sql,
            {
                "lower": observed_at - timedelta(seconds=LOOKBACK_SECONDS),
                "observed_at": observed_at,
                "source_upper": observed_at + timedelta(
                    seconds=MAX_FUTURE_SKEW_SECONDS
                ),
                "limit": ROW_LIMIT,
            },
        ).mappings()
    ]


def _sample_category(rows, *, category: str, observed_at: datetime) -> dict[str, Any]:
    usable: list[dict[str, Any]] = []
    for row in rows:
        if category != "ticker_any" and _bybit_price(row) is None:
            continue
        source_at = _utc(row["source_timestamp"])
        received_at = _utc(row["received_at"])
        source_age = (observed_at - source_at).total_seconds()
        transport_lag = (received_at - source_at).total_seconds()
        usable.append({
            "row_id": int(row["id"]),
            "source_age_seconds": source_age,
            "received_age_seconds": (observed_at - received_at).total_seconds(),
            "eligible": (
                -MAX_FUTURE_SKEW_SECONDS <= source_age <= MAX_SOURCE_AGE_SECONDS
                and transport_lag >= -MAX_FUTURE_SKEW_SECONDS
                and received_at <= observed_at
            ),
        })
    # V4 selection is by source-time freshness, not just received timestamp.
    nearest = min(
        usable,
        key=lambda item: (
            abs(item["source_age_seconds"]),
            -item["row_id"],
        ),
        default=None,
    )
    return {
        "present": bool(usable),
        "eligible": any(item["eligible"] for item in usable),
        "nearest_row_id": nearest["row_id"] if nearest else None,
        "nearest_source_age_seconds": (
            nearest["source_age_seconds"] if nearest else None
        ),
        "nearest_received_age_seconds": (
            nearest["received_age_seconds"] if nearest else None
        ),
        "row_limit_reached": len(rows) == ROW_LIMIT,
    }


def build_report(
    connection,
    *,
    samples: int,
    interval_seconds: float,
) -> dict[str, Any]:
    if samples < 2 or samples > 240:
        raise ValueError("samples must be between 2 and 240")
    if not 0.25 <= interval_seconds <= 5:
        raise ValueError("interval must be between 0.25 and 5 seconds")
    if str(connection.execute(text("SHOW default_transaction_read_only")).scalar_one()) != "on":
        raise SystemExit("database connection is not read-only")

    by_kind: dict[str, dict[str, Any]] = {
        key: {
            "query_timeout_count": 0,
            "query_error_count": 0,
            "no_usable_row_count": 0,
            "eligible_sample_count": 0,
            "sample_success_count": 0,
            "row_limit_reached_count": 0,
            "source_age_seconds": [],
            "received_age_seconds": [],
            "query_seconds": [],
            "latest_row_change_count": 0,
            "last_row_id": None,
        }
        for key in CATEGORIES
    }
    availability = {
        "both_price_classes_eligible": 0,
        "ticker_price_only_eligible": 0,
        "trade_price_only_eligible": 0,
        "neither_price_class_eligible": 0,
        "inconclusive_due_to_query_error": 0,
    }
    started_at = datetime.now(UTC)
    for index in range(samples):
        observed_at = datetime.now(UTC)
        results: dict[str, bool | None] = {}
        for category, state in by_kind.items():
            started = time.perf_counter()
            try:
                rows = _load_rows(
                    connection, category=category, observed_at=observed_at
                )
                result = _sample_category(
                    rows, category=category, observed_at=observed_at
                )
            except OperationalError as exc:
                connection.rollback()
                key = (
                    "query_timeout_count"
                    if "statement timeout" in str(exc).lower()
                    else "query_error_count"
                )
                state[key] += 1
                results[category] = None
                continue

            state["query_seconds"].append(time.perf_counter() - started)
            state["sample_success_count"] += 1
            results[category] = result["eligible"]
            if not result["present"]:
                state["no_usable_row_count"] += 1
            if result["eligible"]:
                state["eligible_sample_count"] += 1
            if result["row_limit_reached"]:
                state["row_limit_reached_count"] += 1
            if result["nearest_row_id"] is not None:
                if (
                    state["last_row_id"] is not None
                    and state["last_row_id"] != result["nearest_row_id"]
                ):
                    state["latest_row_change_count"] += 1
                state["last_row_id"] = result["nearest_row_id"]
                state["source_age_seconds"].append(
                    result["nearest_source_age_seconds"]
                )
                state["received_age_seconds"].append(
                    result["nearest_received_age_seconds"]
                )

        ticker = results.get("ticker_last_price")
        trade = results.get("trade_price")
        if ticker is None or trade is None:
            availability["inconclusive_due_to_query_error"] += 1
        elif ticker and trade:
            availability["both_price_classes_eligible"] += 1
        elif ticker:
            availability["ticker_price_only_eligible"] += 1
        elif trade:
            availability["trade_price_only_eligible"] += 1
        else:
            availability["neither_price_class_eligible"] += 1

        if index + 1 < samples:
            time.sleep(interval_seconds)

    completed_at = datetime.now(UTC)
    compact = {}
    for category, state in by_kind.items():
        compact[category] = {
            key: state[key]
            for key in (
                "sample_success_count",
                "query_timeout_count",
                "query_error_count",
                "no_usable_row_count",
                "eligible_sample_count",
                "row_limit_reached_count",
                "latest_row_change_count",
            )
        }
        for metric in (
            "source_age_seconds",
            "received_age_seconds",
            "query_seconds",
        ):
            compact[category][metric] = _distribution(state[metric])

    return {
        "report": "v4_linear_price_visibility_v1",
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "samples": samples,
        "interval_seconds": interval_seconds,
        "lookback_seconds": LOOKBACK_SECONDS,
        "row_limit_per_query": ROW_LIMIT,
        "max_source_age_seconds": MAX_SOURCE_AGE_SECONDS,
        "max_future_skew_seconds": MAX_FUTURE_SKEW_SECONDS,
        "event_categories": compact,
        "price_eligibility_by_sample": availability,
        "limitations": [
            "A historical received_at timestamp does not prove commit visibility at that time.",
            "Visible event age combines source sparsity, ingestion, buffering, and commit delay.",
            "Repeated queries are not synchronized to the original shadow decision time.",
            "Limited candidate samples may omit a valid price; see row_limit_reached_count.",
            "Sampled read-only queries may add some database load.",
            "This does not evaluate trading profitability or authorize source-policy changes.",
        ],
        "safety": {
            "database_read_only_required": True,
            "database_writes_performed": False,
            "order_submission_performed": False,
            "service_mutation_performed": False,
            "wallet_material_loaded": False,
            "model_refit_performed": False,
            "threshold_tuning_performed": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only Bybit linear partial-ticker versus price-visibility probe."
    )
    parser.add_argument("--env-file", default="/etc/bp/bp.env")
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    parser.add_argument(
        "--interval-seconds", type=float, default=DEFAULT_INTERVAL_SECONDS
    )
    args = parser.parse_args()
    settings = Settings(_env_file=args.env_file)
    if settings.mode is not TradingMode.RESEARCH:
        raise SystemExit("MODE must be research")
    if settings.live_trading_enabled:
        raise SystemExit("LIVE_TRADING_ENABLED must be false")
    if settings.max_trade_size_usd != 0 or settings.max_daily_loss_usd != 0:
        raise SystemExit("risk limits must be zero")
    if settings.recorder_batch_size != 100:
        raise SystemExit("RECORDER_BATCH_SIZE must be 100")
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={
            "options": (
                "-c default_transaction_read_only=on "
                "-c statement_timeout=1500 "
                "-c application_name=bp-v4-linear-price-visibility"
            )
        },
    )
    try:
        with engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            report = build_report(
                connection,
                samples=args.samples,
                interval_seconds=args.interval_seconds,
            )
    finally:
        engine.dispose()
    print(json.dumps(report, indent=2, sort_keys=True))
    print("PHASE14_V4_LINEAR_PRICE_VISIBILITY_STATUS=PASS")
    print("DATABASE_ACCESS=read_only")
    print("DATABASE_WRITES_PERFORMED=false")
    print("SERVICE_MUTATION_PERFORMED=false")
    print("ORDER_SUBMISSION_PERFORMED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
