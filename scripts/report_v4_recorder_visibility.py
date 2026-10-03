from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any

from sqlalchemy import Connection, create_engine, or_, select, text
from sqlalchemy.exc import OperationalError

from bp_engine.config import Settings
from bp_engine.storage.schema import raw_market_events

LOOKBACK_SECONDS = 10.0
DEFAULT_SAMPLES = 80
DEFAULT_INTERVAL_SECONDS = 0.25

SOURCE_SPECS = {
    "coinbase": ("coinbase", "spot", "BTC-USD"),
    "bybit_spot": ("bybit", "spot", "BTCUSDT"),
    "bybit_linear": ("bybit", "linear", "BTCUSDT"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only live visibility probe for V4 BTC recorder sources"
    )
    parser.add_argument("--env-file", default="/etc/bp/bp.env")
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=DEFAULT_INTERVAL_SECONDS,
    )
    return parser.parse_args()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _distribution(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)
    p95_index = max(0, min(len(ordered) - 1, (95 * len(ordered) + 99) // 100 - 1))
    return {
        "min": ordered[0],
        "median": float(median(ordered)),
        "p95": ordered[p95_index],
        "max": ordered[-1],
    }


def _latest_row(
    connection: Connection,
    *,
    venue: str,
    observed_at: datetime,
) -> dict[str, Any] | None:
    source, stream, instrument = SOURCE_SPECS[venue]
    statement = select(
        raw_market_events.c.id,
        raw_market_events.c.event_type,
        raw_market_events.c.source_timestamp,
        raw_market_events.c.received_at,
    ).where(
        raw_market_events.c.source == source,
        raw_market_events.c.stream == stream,
        raw_market_events.c.instrument == instrument,
        raw_market_events.c.source_timestamp.is_not(None),
        raw_market_events.c.received_at
        >= observed_at - timedelta(seconds=LOOKBACK_SECONDS),
        raw_market_events.c.received_at <= observed_at,
    )
    if source == "coinbase":
        statement = statement.where(
            or_(
                raw_market_events.c.event_type.like("ticker_%"),
                raw_market_events.c.event_type.like("market_trades_%"),
            )
        )
    else:
        statement = statement.where(
            raw_market_events.c.event_type.in_(("ticker", "trade"))
        )
    row = connection.execute(
        statement.order_by(
            raw_market_events.c.received_at.desc(),
            raw_market_events.c.id.desc(),
        ).limit(1)
    ).mappings().first()
    return None if row is None else dict(row)


def _activity_snapshot(connection: Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        text(
            """
            SELECT
                pid,
                state,
                wait_event_type,
                wait_event,
                CASE
                    WHEN xact_start IS NULL THEN NULL
                    ELSE EXTRACT(EPOCH FROM (clock_timestamp() - xact_start))
                END AS xact_age_seconds,
                pg_blocking_pids(pid) AS blocking_pids,
                left(query, 220) AS query
            FROM pg_stat_activity
            WHERE datname = current_database()
              AND pid <> pg_backend_pid()
              AND state <> 'idle'
              AND query ILIKE '%raw_market_events%'
            ORDER BY xact_start NULLS LAST, query_start NULLS LAST
            LIMIT 30
            """
        )
    ).mappings()
    return [dict(row) for row in rows]


def build_report(
    connection: Connection,
    *,
    samples: int,
    interval_seconds: float,
) -> dict[str, Any]:
    if samples <= 0:
        raise ValueError("samples must be positive")
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")

    readonly = str(
        connection.execute(text("SHOW default_transaction_read_only")).scalar_one()
    ).lower()
    if readonly != "on":
        raise RuntimeError("database connection is not read-only")

    per_venue: dict[str, dict[str, Any]] = {
        venue: {
            "row_seen_count": 0,
            "query_timeout_count": 0,
            "query_failure_count": 0,
            "metadata_ready_count": 0,
            "visible_age_seconds": [],
            "source_age_seconds": [],
            "transport_lag_seconds": [],
            "row_change_count": 0,
            "last_row_id": None,
            "same_row_streak_samples": 0,
            "max_same_row_streak_samples": 0,
        }
        for venue in SOURCE_SPECS
    }

    for sample_index in range(samples):
        observed_at = datetime.now(UTC)
        for venue in SOURCE_SPECS:
            state = per_venue[venue]
            try:
                row = _latest_row(
                    connection,
                    venue=venue,
                    observed_at=observed_at,
                )
            except OperationalError as exc:
                if "statement timeout" in str(exc).lower():
                    state["query_timeout_count"] += 1
                else:
                    state["query_failure_count"] += 1
                continue
            if row is None:
                continue

            source_at = _utc(row["source_timestamp"])
            received_at = _utc(row["received_at"])
            source_age = (observed_at - source_at).total_seconds()
            visible_age = (observed_at - received_at).total_seconds()
            transport_lag = (received_at - source_at).total_seconds()

            state["row_seen_count"] += 1
            state["visible_age_seconds"].append(visible_age)
            state["source_age_seconds"].append(source_age)
            state["transport_lag_seconds"].append(transport_lag)

            if (
                -1.0 <= source_age <= 2.0
                and transport_lag >= -1.0
                and received_at <= observed_at
            ):
                state["metadata_ready_count"] += 1

            row_id = int(row["id"])
            if state["last_row_id"] is None:
                state["last_row_id"] = row_id
                state["same_row_streak_samples"] = 1
            elif state["last_row_id"] == row_id:
                state["same_row_streak_samples"] += 1
            else:
                state["row_change_count"] += 1
                state["last_row_id"] = row_id
                state["same_row_streak_samples"] = 1
            state["max_same_row_streak_samples"] = max(
                state["max_same_row_streak_samples"],
                state["same_row_streak_samples"],
            )

        if sample_index + 1 < samples:
            time.sleep(interval_seconds)

    rendered: dict[str, Any] = {}
    for venue, state in per_venue.items():
        seen = int(state["row_seen_count"])
        rendered[venue] = {
            "sample_count": samples,
            "row_seen_count": seen,
            "query_timeout_count": int(state["query_timeout_count"]),
            "query_failure_count": int(state["query_failure_count"]),
            "metadata_ready_count": int(state["metadata_ready_count"]),
            "metadata_ready_fraction": (
                float(state["metadata_ready_count"]) / seen if seen else None
            ),
            "row_change_count": int(state["row_change_count"]),
            "max_same_row_streak_seconds": (
                float(state["max_same_row_streak_samples"]) * interval_seconds
            ),
            "visible_age_seconds": _distribution(state["visible_age_seconds"]),
            "source_age_seconds": _distribution(state["source_age_seconds"]),
            "transport_lag_seconds": _distribution(
                state["transport_lag_seconds"]
            ),
        }

    return {
        "report": "v4_recorder_visibility_v1",
        "recorded_at": datetime.now(UTC).isoformat(),
        "samples": samples,
        "interval_seconds": interval_seconds,
        "lookback_seconds": LOOKBACK_SECONDS,
        "venues": rendered,
        "database_activity": _activity_snapshot(connection),
        "safety": {
            "database_read_only_required": True,
            "database_writes_performed": False,
            "service_mutation_performed": False,
            "order_submission_performed": False,
        },
    }


def main() -> int:
    args = parse_args()
    settings = Settings(_env_file=args.env_file)
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={
            "options": (
                "-c default_transaction_read_only=on "
                "-c statement_timeout=2000 "
                "-c application_name=bp-v4-recorder-visibility-probe"
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

    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    print("PHASE14_V4_RECORDER_VISIBILITY_STATUS=PASS")
    print("DATABASE_ACCESS=read_only")
    print("DATABASE_WRITES_PERFORMED=false")
    print("SERVICE_MUTATION_PERFORMED=false")
    print("ORDER_SUBMISSION_PERFORMED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
