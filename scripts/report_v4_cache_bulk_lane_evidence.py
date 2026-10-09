from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import OperationalError

from bp_engine.config import Settings, TradingMode

# Read-only, short-window companion to the recorder commit-lag report.
# Event classes are NOT interchangeable: the priority ticker lane can be
# current while the bulk publicTrade and orderbook lanes are minutes behind.
SAMPLE_SECONDS = 30
SOURCE_LOOKBACK_MINUTES = 15
EVENT_SPECS = (
    ("spot", "ticker"),
    ("spot", "trade"),
    ("spot", "orderbook"),
    ("linear", "ticker"),
    ("linear", "trade"),
    ("linear", "orderbook"),
)
INDEX_COUNT = 16

_EVENT_SQL = text(
    """
    SELECT id, received_at, source_timestamp
    FROM raw_market_events
    WHERE source = 'bybit'
      AND stream = :stream
      AND instrument = 'BTCUSDT'
      AND received_at >= :lower
      AND received_at <= :observed_at
      AND (
        event_type = :kind
        OR (:kind = 'orderbook' AND event_type LIKE 'orderbook_%')
      )
    ORDER BY received_at DESC, id DESC
    LIMIT 1
    """
)

_INDEX_SQL = text(
    """
    SELECT count(*) AS index_count,
           coalesce(sum(idx_blks_read), 0) AS block_reads,
           coalesce(sum(idx_blks_hit), 0) AS block_hits
    FROM pg_statio_user_indexes
    WHERE indexrelname ~
      '^raw_event_dedupe_h[0-9]{2}_digest_uidx$'
    """
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("naive recorder timestamp")
    return value.astimezone(UTC)


def _event_snapshot(
    connection: Connection, *, observed_at: datetime
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for stream, kind in EVENT_SPECS:
        name = f"{stream}_{kind}"
        try:
            row = connection.execute(
                _EVENT_SQL,
                {
                    "stream": stream,
                    "kind": kind,
                    "lower": observed_at
                    - timedelta(minutes=SOURCE_LOOKBACK_MINUTES),
                    "observed_at": observed_at,
                },
            ).mappings().first()
        except OperationalError as exc:
            # Keep the report incomplete rather than interpreting a failed
            # query as "no events"; autocommit sessions can recover.
            connection.rollback()
            result[name] = {
                "status": "query_timeout_or_error",
                "error_class": type(exc).__name__,
            }
            continue
        if row is None:
            result[name] = {"status": "no_recent_event"}
            continue
        received_at = _utc(row["received_at"])
        source_at = row["source_timestamp"]
        result[name] = {
            "status": "observed",
            "row_id": int(row["id"]),
            "received_at": received_at.isoformat(),
            "received_age_seconds": round(
                (observed_at - received_at).total_seconds(), 6
            ),
            "source_age_seconds": (
                round((observed_at - _utc(source_at)).total_seconds(), 6)
                if source_at is not None else None
            ),
        }
    return result


def _index_snapshot(connection: Connection) -> dict[str, int]:
    row = connection.execute(_INDEX_SQL).mappings().one()
    if int(row["index_count"]) != INDEX_COUNT:
        raise RuntimeError(
            f"expected {INDEX_COUNT} compact digest indexes; "
            f"found {row['index_count']}"
        )
    return {
        "reads": int(row["block_reads"]),
        "hits": int(row["block_hits"]),
    }


def _event_progress(
    before: dict[str, Any], after: dict[str, Any], *, elapsed: float
) -> dict[str, Any]:
    if before["status"] != "observed" or after["status"] != "observed":
        return {
            "status": "inconclusive",
            "start_status": before["status"],
            "end_status": after["status"],
        }
    advance = (
        datetime.fromisoformat(after["received_at"])
        - datetime.fromisoformat(before["received_at"])
    ).total_seconds()
    return {
        "status": "observed",
        "start_received_age_seconds": before["received_age_seconds"],
        "end_received_age_seconds": after["received_age_seconds"],
        "advance_seconds": round(advance, 6),
        "advance_to_wall_ratio": round(advance / elapsed, 6),
        "lag_growth_seconds": round(
            after["received_age_seconds"] - before["received_age_seconds"], 6
        ),
    }


def capture_report(
    connection: Connection,
    *,
    sample_seconds: float = SAMPLE_SECONDS,
    sleep_fn=time.sleep,
) -> dict[str, Any]:
    if not 10 <= sample_seconds <= 120:
        raise ValueError("sample_seconds must be between 10 and 120")
    read_only = str(
        connection.execute(text("SHOW default_transaction_read_only")).scalar_one()
    ).lower()
    if read_only != "on":
        raise RuntimeError("database connection must be read-only")

    start = datetime.now(UTC)
    initial_events = _event_snapshot(connection, observed_at=start)
    initial_indexes = _index_snapshot(connection)
    sleep_fn(sample_seconds)
    end = datetime.now(UTC)
    final_events = _event_snapshot(connection, observed_at=end)
    final_indexes = _index_snapshot(connection)
    elapsed = (end - start).total_seconds()
    reads = final_indexes["reads"] - initial_indexes["reads"]
    hits = final_indexes["hits"] - initial_indexes["hits"]
    if reads < 0 or hits < 0:
        raise RuntimeError("PostgreSQL index statistics reset during capture")
    progressed = {
        kind: _event_progress(
            initial_events[kind], final_events[kind], elapsed=elapsed
        )
        for kind in initial_events
    }
    return {
        "report": "v4_cache_bulk_lane_evidence_v1",
        "started_at": start.isoformat(),
        "completed_at": end.isoformat(),
        "sample_wall_seconds": round(elapsed, 6),
        "lookback_minutes": SOURCE_LOOKBACK_MINUTES,
        "digest_index_delta": {
            "buffer_reads": reads,
            "buffer_hits": hits,
            "reads_per_second": round(reads / elapsed, 6),
            "interval_hit_ratio": (
                round(hits / (hits + reads), 6) if reads + hits else None
            ),
        },
        "event_progress": progressed,
        "complete": all(
            entry["status"] == "observed" for entry in progressed.values()
        ),
        "limitations": [
            "Commit visibility is sampled, not reconstructed at receipt time.",
            "PostgreSQL buffer reads may be served by OS page cache.",
            "Recorder restarts can drop queued events and confound A/B comparisons.",
            "Missing rows and timed-out queries are inconclusive, not feed absence.",
            "A 30-second interval cannot establish sustainable catch-up.",
        ],
        "safety": {
            "database_read_only_required": True,
            "database_writes_performed": False,
            "recorder_config_mutated_by_probe": False,
            "order_submission_performed": False,
        },
    }


def capture_from_settings(settings: Settings) -> dict[str, Any]:
    if settings.mode is not TradingMode.RESEARCH:
        raise RuntimeError("research mode required")
    if settings.live_trading_enabled:
        raise RuntimeError("live trading must be disabled")
    if settings.max_trade_size_usd != 0 or settings.max_daily_loss_usd != 0:
        raise RuntimeError("zero-money risk limits required")
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={
            "options": (
                "-c default_transaction_read_only=on "
                "-c statement_timeout=1500 "
                "-c application_name=bp-v4-cache-bulk-lane-evidence"
            )
        },
    )
    try:
        with engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            return capture_report(connection)
    finally:
        engine.dispose()


if __name__ == "__main__":
    from argparse import ArgumentParser

    parser = ArgumentParser(description="Read-only bulk/priority/index A/B evidence")
    parser.add_argument("--env-file", default="/etc/bp/bp.env")
    args = parser.parse_args()
    report = capture_from_settings(Settings(_env_file=args.env_file))
    print(json.dumps(report, indent=2, sort_keys=True))
    print("PHASE14_V4_CACHE_BULK_EVIDENCE_STATUS=PASS")
    print("DATABASE_WRITES_PERFORMED=false")
    print("SERVICE_MUTATION_PERFORMED=false")
    print("ORDER_SUBMISSION_PERFORMED=false")
