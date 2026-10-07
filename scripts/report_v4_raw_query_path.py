from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from bp_engine.config import Settings
from bp_engine.v4_paper.inference import FROZEN_V4_OFFSET_SECONDS

_SOURCE_SPECS = {
    "coinbase": ("coinbase", "spot", "BTC-USD"),
    "bybit_spot": ("bybit", "spot", "BTCUSDT"),
    "bybit_linear": ("bybit", "linear", "BTCUSDT"),
}
_RECEIVED_WINDOW_SECONDS = 4.0


def _parse_iso_utc(value: object, name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise SystemExit(f"{name} must be an ISO timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SystemExit(f"{name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _representative_decision_at(path: Path) -> datetime:
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            if record.get("event") != "v4_fresh_book_shadow_source_ineligible":
                continue
            return _parse_iso_utc(record.get("decision_at"), "decision_at")
    raise SystemExit("no source-ineligible decision found in evidence")


def _partition_name(value: datetime) -> str:
    utc = value.astimezone(UTC)
    return f"raw_market_events_{utc:%Y%m%d}_{utc:%H}"


def _relation_info(connection, name: str) -> dict[str, Any] | None:
    row = connection.execute(
        text(
            """
            SELECT
                relation.relkind,
                relation.reltuples::bigint AS estimated_rows,
                pg_total_relation_size(relation.oid) AS total_bytes
            FROM pg_class AS relation
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = current_schema()
              AND relation.relname = :name
            """
        ),
        {"name": name},
    ).mappings().one_or_none()
    return None if row is None else dict(row)


def _indexes(connection, table_name: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        text(
            """
            SELECT
                index_relation.relname AS indexname,
                pg_get_indexdef(index_relation.oid) AS indexdef,
                index_metadata.indisvalid,
                index_metadata.indisready
            FROM pg_class AS table_relation
            JOIN pg_namespace AS table_namespace
              ON table_namespace.oid = table_relation.relnamespace
            JOIN pg_index AS index_metadata
              ON index_metadata.indrelid = table_relation.oid
            JOIN pg_class AS index_relation
              ON index_relation.oid = index_metadata.indexrelid
            WHERE table_namespace.nspname = current_schema()
              AND table_relation.relname = :table_name
            ORDER BY index_relation.relname
            """
        ),
        {"table_name": table_name},
    ).mappings()
    return [dict(row) for row in rows]


def _activity_snapshot(connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        text(
            """
            SELECT
                pid,
                usename,
                application_name,
                state,
                wait_event_type,
                wait_event,
                query_start,
                xact_start,
                backend_type,
                pg_blocking_pids(pid) AS blocking_pids,
                left(query, 240) AS query
            FROM pg_stat_activity
            WHERE datname = current_database()
              AND pid <> pg_backend_pid()
              AND state <> 'idle'
            ORDER BY query_start NULLS LAST
            LIMIT 20
            """
        )
    ).mappings()
    return [dict(row) for row in rows]


def _safe_section(connection, callback) -> dict[str, Any]:
    try:
        return {"status": "ok", "value": callback()}
    except OperationalError as exc:
        connection.rollback()
        return {
            "status": (
                "timeout"
                if "statement timeout" in str(exc).lower()
                else "failed"
            ),
            "error": str(exc).splitlines()[0],
        }


def _attached_to_parent(connection, child_name: str) -> bool:
    return bool(
        connection.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_inherits
                    JOIN pg_class AS parent ON parent.oid = pg_inherits.inhparent
                    JOIN pg_class AS child ON child.oid = pg_inherits.inhrelid
                    JOIN pg_namespace AS namespace ON namespace.oid = parent.relnamespace
                    WHERE namespace.nspname = current_schema()
                      AND parent.relname = 'raw_market_events'
                      AND child.relname = :child_name
                )
                """
            ),
            {"child_name": child_name},
        ).scalar_one()
    )


def _explain(connection, *, venue: str, requested_at: datetime) -> dict[str, Any]:
    source, stream, instrument = _SOURCE_SPECS[venue]
    received_lower = requested_at - timedelta(seconds=_RECEIVED_WINDOW_SECONDS)
    source_lower = requested_at - timedelta(seconds=2)
    source_upper = requested_at + timedelta(seconds=1)
    if source == "coinbase":
        event_clause = (
            "(event_type LIKE 'ticker_%' OR "
            "event_type LIKE 'market_trades_%')"
        )
    else:
        event_clause = "event_type IN ('ticker', 'trade')"

    sql = f"""
        EXPLAIN (FORMAT JSON)
        SELECT
            id,
            source,
            stream,
            instrument,
            event_type,
            source_timestamp,
            received_at,
            sequence,
            market_id,
            asset_id,
            payload,
            dedupe_key
        FROM raw_market_events
        WHERE source = :source
          AND stream = :stream
          AND instrument = :instrument
          AND source_timestamp IS NOT NULL
          AND received_at >= :received_lower
          AND received_at <= :requested_at
          AND source_timestamp >= :source_lower
          AND source_timestamp <= :source_upper
          AND {event_clause}
        ORDER BY received_at DESC, id DESC
    """
    try:
        plan = connection.execute(
            text(sql),
            {
                "source": source,
                "stream": stream,
                "instrument": instrument,
                "received_lower": received_lower,
                "requested_at": requested_at,
                "source_lower": source_lower,
                "source_upper": source_upper,
            },
        ).scalar_one()
        return {
            "status": "ok",
            "requested_at": requested_at.isoformat(),
            "received_lower": received_lower.isoformat(),
            "source_lower": source_lower.isoformat(),
            "source_upper": source_upper.isoformat(),
            "plan": plan,
        }
    except OperationalError as exc:
        connection.rollback()
        return {
            "status": (
                "timeout"
                if "statement timeout" in str(exc).lower()
                else "failed"
            ),
            "error": str(exc).splitlines()[0],
        }


def build_report(
    connection,
    evidence_file: Path,
    *,
    requested_at: datetime | None = None,
    venue: str | None = None,
) -> dict[str, Any]:
    decision_at = _representative_decision_at(evidence_file)
    market_start_at = decision_at - timedelta(seconds=FROZEN_V4_OFFSET_SECONDS)
    cutoffs = {
        "market_start": market_start_at,
        "current": decision_at,
    }

    partitions: dict[str, Any] = {}
    for label, cutoff in cutoffs.items():
        name = _partition_name(cutoff)
        partitions[label] = {
            "cutoff": cutoff.isoformat(),
            "partition": name,
            "relation": _safe_section(
                connection,
                lambda name=name: _relation_info(connection, name),
            ),
            "attached": _safe_section(
                connection,
                lambda name=name: _attached_to_parent(connection, name),
            ),
            "indexes": _safe_section(
                connection,
                lambda name=name: _indexes(connection, name),
            ),
        }

    exact_query = None
    if requested_at is not None:
        if venue is None:
            raise SystemExit("--venue is required with --requested-at")
        partition = _partition_name(requested_at)
        exact_query = {
            "venue": venue,
            "requested_at": requested_at.isoformat(),
            "partition": partition,
            "partition_relation": _safe_section(
                connection,
                lambda: _relation_info(connection, partition),
            ),
            "partition_indexes": _safe_section(
                connection,
                lambda: _indexes(connection, partition),
            ),
            "explain": _explain(
                connection,
                venue=venue,
                requested_at=requested_at,
            ),
        }

    return {
        "report": "v4_raw_query_path_v3",
        "evidence_file": evidence_file.name,
        "representative_decision_at": decision_at.isoformat(),
        "parent_relation": _safe_section(
            connection,
            lambda: _relation_info(connection, "raw_market_events"),
        ),
        "parent_indexes": _safe_section(
            connection,
            lambda: _indexes(connection, "raw_market_events"),
        ),
        "database_activity": _safe_section(
            connection,
            lambda: _activity_snapshot(connection),
        ),
        "current_cutoff_explain": {
            venue_name: _explain(
                connection,
                venue=venue_name,
                requested_at=decision_at,
            )
            for venue_name in _SOURCE_SPECS
        },
        "exact_query": exact_query,
        "partitions": partitions,
        "safety": {
            "database_read_only_required": True,
            "database_writes_performed": False,
            "service_mutation_performed": False,
            "order_submission_performed": False,
        },
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect the V4 raw-event partition/index query path without scanning payloads."
    )
    parser.add_argument("--env-file", default="/etc/bp/bp.env")
    parser.add_argument("--evidence-file", required=True)
    parser.add_argument("--requested-at")
    parser.add_argument("--venue", choices=tuple(_SOURCE_SPECS))
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    evidence_file = Path(args.evidence_file)
    if not evidence_file.is_file():
        raise SystemExit(f"evidence file missing: {evidence_file}")

    settings = Settings(_env_file=args.env_file)
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={
            "options": "-c default_transaction_read_only=on -c statement_timeout=3000"
        },
    )
    try:
        with engine.connect() as connection:
            if connection.execute(text("SHOW default_transaction_read_only")).scalar_one() != "on":
                raise SystemExit("database connection is not read-only")
            report = build_report(
                connection,
                evidence_file,
                requested_at=(
                    None
                    if args.requested_at is None
                    else _parse_iso_utc(args.requested_at, "requested_at")
                ),
                venue=args.venue,
            )
    finally:
        engine.dispose()

    print(json.dumps(report, sort_keys=True, indent=2, default=str))
    print("PHASE14_V4_RAW_QUERY_PATH_STATUS=PASS")
    print("DATABASE_ACCESS=read_only")
    print("DATABASE_WRITES_PERFORMED=false")
    print("SERVICE_MUTATION_PERFORMED=false")
    print("ORDER_SUBMISSION_PERFORMED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
