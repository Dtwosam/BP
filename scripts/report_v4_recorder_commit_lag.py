from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from statistics import median
from typing import Any

from sqlalchemy import Connection, create_engine, or_, select, text
from sqlalchemy.exc import OperationalError

from bp_engine.config import Settings
from bp_engine.storage.schema import raw_market_events

DEFAULT_SAMPLES = 60
DEFAULT_INTERVAL_SECONDS = 0.5

SOURCE_SPECS = {
    "coinbase": ("coinbase", "spot", "BTC-USD"),
    "bybit_spot": ("bybit", "spot", "BTCUSDT"),
    "bybit_linear": ("bybit", "linear", "BTCUSDT"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only committed-row lag probe for V4 BTC recorder sources"
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


def _latest_v4_row(
    connection: Connection,
    *,
    venue: str,
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


def _active_raw_writers(connection: Connection) -> list[dict[str, Any]]:
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
                CASE
                    WHEN query_start IS NULL THEN NULL
                    ELSE EXTRACT(EPOCH FROM (clock_timestamp() - query_start))
                END AS query_age_seconds,
                left(query, 180) AS query
            FROM pg_stat_activity
            WHERE datname = current_database()
              AND pid <> pg_backend_pid()
              AND state <> 'idle'
              AND (
                    query ILIKE '%INSERT INTO raw_market_events%'
                 OR query ILIKE '%INSERT INTO raw_event_dedupe%'
              )
            ORDER BY xact_start NULLS LAST, query_start NULLS LAST
            LIMIT 20
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
    if samples <= 1:
        raise ValueError("samples must be greater than one")
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")

    readonly = str(
        connection.execute(text("SHOW default_transaction_read_only")).scalar_one()
    ).lower()
    if readonly != "on":
        raise RuntimeError("database connection is not read-only")

    venue_state: dict[str, dict[str, Any]] = {
        venue: {
            "observed_at": [],
            "received_at": [],
            "commit_lag_seconds": [],
            "row_ids": [],
            "query_timeout_count": 0,
            "query_failure_count": 0,
        }
        for venue in SOURCE_SPECS
    }
    writer_xact_ages: list[float] = []
    writer_query_ages: list[float] = []
    writer_active_sample_count = 0
    writer_wait_counts: dict[str, int] = {}

    started_at = datetime.now(UTC)
    for sample_index in range(samples):
        observed_at = datetime.now(UTC)

        for venue in SOURCE_SPECS:
            state = venue_state[venue]
            try:
                row = _latest_v4_row(connection, venue=venue)
            except OperationalError as exc:
                if "statement timeout" in str(exc).lower():
                    state["query_timeout_count"] += 1
                else:
                    state["query_failure_count"] += 1
                continue
            if row is None:
                continue

            received_at = _utc(row["received_at"])
            state["observed_at"].append(observed_at)
            state["received_at"].append(received_at)
            state["commit_lag_seconds"].append(
                max(0.0, (observed_at - received_at).total_seconds())
            )
            state["row_ids"].append(int(row["id"]))

        try:
            writers = _active_raw_writers(connection)
        except OperationalError:
            writers = []
        if writers:
            writer_active_sample_count += 1
        for writer in writers:
            xact_age = writer.get("xact_age_seconds")
            query_age = writer.get("query_age_seconds")
            if xact_age is not None:
                writer_xact_ages.append(float(xact_age))
            if query_age is not None:
                writer_query_ages.append(float(query_age))
            wait_key = (
                f"{writer.get('wait_event_type') or 'none'}:"
                f"{writer.get('wait_event') or 'none'}"
            )
            writer_wait_counts[wait_key] = writer_wait_counts.get(wait_key, 0) + 1

        if sample_index + 1 < samples:
            time.sleep(interval_seconds)

    completed_at = datetime.now(UTC)
    venues: dict[str, Any] = {}
    for venue, state in venue_state.items():
        observed = state["observed_at"]
        received = state["received_at"]
        row_ids = state["row_ids"]
        row_change_count = sum(
            1
            for left, right in zip(row_ids, row_ids[1:], strict=True)
            if left != right
        )
        wall_seconds = (
            (observed[-1] - observed[0]).total_seconds()
            if len(observed) >= 2
            else 0.0
        )
        committed_advance_seconds = (
            (received[-1] - received[0]).total_seconds()
            if len(received) >= 2
            else 0.0
        )
        advancement_ratio = (
            committed_advance_seconds / wall_seconds
            if wall_seconds > 0
            else None
        )
        venues[venue] = {
            "sample_count": samples,
            "row_seen_count": len(row_ids),
            "query_timeout_count": int(state["query_timeout_count"]),
            "query_failure_count": int(state["query_failure_count"]),
            "row_change_count": row_change_count,
            "first_committed_received_at": (
                received[0].isoformat() if received else None
            ),
            "last_committed_received_at": (
                received[-1].isoformat() if received else None
            ),
            "commit_lag_seconds": _distribution(state["commit_lag_seconds"]),
            "sample_wall_seconds": wall_seconds,
            "committed_received_at_advance_seconds": committed_advance_seconds,
            "commit_advancement_ratio": advancement_ratio,
            "lag_delta_seconds": (
                state["commit_lag_seconds"][-1] - state["commit_lag_seconds"][0]
                if len(state["commit_lag_seconds"]) >= 2
                else None
            ),
        }

    return {
        "report": "v4_recorder_commit_lag_v1",
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "samples": samples,
        "interval_seconds": interval_seconds,
        "venues": venues,
        "writer_activity": {
            "active_sample_count": writer_active_sample_count,
            "sample_count": samples,
            "xact_age_seconds": _distribution(writer_xact_ages),
            "query_age_seconds": _distribution(writer_query_ages),
            "wait_counts": dict(sorted(writer_wait_counts.items())),
        },
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
                "-c application_name=bp-v4-recorder-commit-lag-probe"
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
    print("PHASE14_V4_RECORDER_COMMIT_LAG_STATUS=PASS")
    print("DATABASE_ACCESS=read_only")
    print("DATABASE_WRITES_PERFORMED=false")
    print("SERVICE_MUTATION_PERFORMED=false")
    print("ORDER_SUBMISSION_PERFORMED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
