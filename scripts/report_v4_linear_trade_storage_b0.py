"""Candidate Stage B0: bounded read-only Bybit-linear trade storage classification.

REVIEW ONLY. This script is NOT approved to execute on a production host.
It reads at most two small received-time-bounded samples and emits no rows
or payload values. Results cannot establish WebSocket packet receipt,
transaction commit timestamps, or historical 7-8 October root causes.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, text

from bp_engine.config import Settings, TradingMode

LOOKBACK_SECONDS = 60
ROW_CAP_PER_CLASS = 1024
STATEMENT_TIMEOUT_MS = 1500
CLASSES = ("trade", "orderbook")


def _classification_sql(event_class: str):
    if event_class not in CLASSES:
        raise ValueError("unsupported event class")
    event_filter = (
        "event_type = 'trade'"
        if event_class == "trade"
        else "event_type IN ('orderbook_snapshot', 'orderbook_delta')"
    )
    return text(
        """
        WITH bounded AS MATERIALIZED (
            SELECT
                id, source_timestamp, received_at,
                payload->'data' AS data,
                payload->'data'->-1->>'p' AS last_trade_price
            FROM raw_market_events
            WHERE source = 'bybit'
              AND stream = 'linear'
              AND instrument = 'BTCUSDT'
              AND received_at >= :lower
              AND received_at <= :observed_at
              AND """
        + event_filter
        + """
            ORDER BY received_at DESC, id DESC
            LIMIT :row_cap
        )
        SELECT
            COUNT(*) AS rows_sampled,
            COUNT(*) FILTER (WHERE source_timestamp IS NULL) AS null_source_ts,
            COUNT(*) FILTER (
                WHERE source_timestamp IS NOT NULL
            ) AS nonnull_source_ts,
            COUNT(*) FILTER (
                WHERE jsonb_typeof(data) = 'array'
            ) AS array_data,
            COUNT(*) FILTER (
                WHERE NULLIF(BTRIM(last_trade_price), '') IS NOT NULL
            ) AS last_trade_price_field,
            COUNT(*) FILTER (
                WHERE source_timestamp IS NOT NULL
                  AND source_timestamp BETWEEN
                    :observed_at - INTERVAL '2 seconds'
                    AND :observed_at + INTERVAL '1 second'
                  AND received_at >= source_timestamp - INTERVAL '1 second'
            ) AS source_time_eligible_at_observed
        FROM bounded
        """
    )


def build_report(connection, *, observed_at: datetime):
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    observed_at = observed_at.astimezone(UTC)

    if connection.dialect.name != "postgresql":
        raise SystemExit("PostgreSQL-only report")
    mode = str(connection.execute(text("SHOW default_transaction_read_only")).scalar_one())
    if mode != "on":
        raise SystemExit("database connection is not read-only")

    stats = {}
    for event_class in CLASSES:
        row = connection.execute(
            _classification_sql(event_class),
            {
                "lower": observed_at - timedelta(seconds=LOOKBACK_SECONDS),
                "observed_at": observed_at,
                "row_cap": ROW_CAP_PER_CLASS,
            },
        ).mappings().one()
        count = int(row["rows_sampled"])
        if not 0 <= count <= ROW_CAP_PER_CLASS:
            raise ValueError("row sample count exceeds reviewed bound")
        stats[event_class] = {
            "rows_sampled": count,
            "capped_inconclusive": count == ROW_CAP_PER_CLASS,
            "null_source_timestamp_rows": int(row["null_source_ts"]),
            "nonnull_source_timestamp_rows": int(row["nonnull_source_ts"]),
            "json_array_payload_rows": int(row["array_data"]),
            "last_trade_price_field_present_rows": int(row["last_trade_price_field"]),
            "source_time_eligible_at_observed_rows": int(
                row["source_time_eligible_at_observed"]
            ),
        }
        if (
            stats[event_class]["null_source_timestamp_rows"]
            + stats[event_class]["nonnull_source_timestamp_rows"]
            != count
        ):
            raise ValueError("timestamp classification fails reconciliation")

    return {
        "report": "v4_bybit_linear_trade_storage_b0_candidate_v1",
        "execution_class": "reviewed_only_requires_separate_production_authorization",
        "observed_at": observed_at.isoformat(),
        "lookback_seconds": LOOKBACK_SECONDS,
        "row_cap_per_class": ROW_CAP_PER_CLASS,
        "max_data_select_queries": len(CLASSES),
        "classes": stats,
        "limitations": [
            "Committed and currently query-visible rows only, not WebSocket packets.",
            "One fixed wall-clock cutoff; not the earlier Stage A cutoffs or 7-8 October.",
            "LIMIT may truncate each event class; capped results are not full counts.",
            "Field-present is not proof of valid positive decimal trade price.",
            "Source-time eligibility is only at the single observed_at cutoff.",
            "No historical queue residence, ingestion latency or commit time is measured.",
            "No production access or extra probe is authorized by this code.",
        ],
        "safety": {
            "database_read_only_required": True,
            "database_writes_performed": False,
            "service_mutation_performed": False,
            "order_submission_performed": False,
            "model_refit_performed": False,
            "threshold_tuning_performed": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="UNAUTHORIZED candidate B0: read-only Bybit linear storage classification."
    )
    parser.add_argument("--env-file", default="/etc/bp/bp.env")
    parser.add_argument(
        "--approval-ack",
        required=True,
        choices=["AUTHORIZED_ONE_TIME_STAGE_B0"],
        help="Only pass after a separately documented explicit user approval.",
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
                "-c application_name=bp-v4-linear-trade-b0-review"
            )
        },
    )
    try:
        with engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            report = build_report(connection, observed_at=datetime.now(UTC))
    finally:
        engine.dispose()

    print(json.dumps(report, indent=2, sort_keys=True))
    print("PHASE14_V4_LINEAR_TRADE_B0_STATUS=PASS_DATA_ONLY")
    print("DATABASE_ACCESS=read_only")
    print("DATABASE_WRITES_PERFORMED=false")
    print("SERVICE_MUTATION_PERFORMED=false")
    print("ORDER_SUBMISSION_PERFORMED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
